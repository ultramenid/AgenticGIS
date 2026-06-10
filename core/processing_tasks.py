"""Helpers for running QGIS Processing algorithms as QgsTask instances.

These helpers are intended for worker-thread callers. QGIS Processing task
objects are constructed and added to the global task manager on the main
thread through ``MainThreadExecutor``; the worker thread only waits on a
``threading.Event`` and asks the main thread to cancel when requested.
"""

import threading
import time

from qgis.core import (
    QgsApplication,
    QgsProcessingAlgRunnerTask,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsTask,
)

from .cancellation import cancel_requested as _cancel_requested
from .dev_logging import log_event


def _stringify_results(results):
    try:
        return {k: str(v) for k, v in (results or {}).items()}
    except BaseException as exc:  # noqa: BLE001
        return {"_serialization_error": f"{type(exc).__name__}: {exc}"}


def _take_result_layers(context, results, sink):
    """Pull temporary output layers out of the finished context.

    QgsProcessingAlgRunnerTask leaves TEMPORARY_OUTPUT layers in the
    context's temporaryLayerStore; once the context is garbage collected
    those layers die and the "output_..." ids in the results dangle.
    Handing each layer to ``sink`` keeps it alive so a later tool call
    (another algorithm, or add_layer) can reference it by id.
    Runs on the main thread (``executed`` is delivered there).
    """
    collected = {}
    if sink is None or context is None:
        return collected
    for key, value in (results or {}).items():
        if not isinstance(value, str):
            continue
        layer = _safe_take_layer(context, value)
        if layer is not None and _safe_sink(sink, layer):
            collected[key] = layer.id()
            count = _safe_feature_count(layer)
            if count is not None:
                collected[f"{key}_feature_count"] = count
    return collected


def _safe_feature_count(layer):
    """Feature count for vector layers; None when unknown/not applicable."""
    try:
        count = int(layer.featureCount())
    except (AttributeError, TypeError, ValueError):
        return None
    except Exception:  # noqa: BLE001 — counting must never break a result
        return None
    return count if count >= 0 else None


def _safe_take_layer(context, layer_id):
    """Take a temporary result layer from the context; None on any failure."""
    try:
        if context.temporaryLayerStore().mapLayer(layer_id) is None:
            return None
        return context.takeResultLayer(layer_id)
    except Exception:  # noqa: BLE001 — never fail the result over retention
        return None


def _safe_sink(sink, layer):
    """Hand a layer to the registry sink; a sink failure must not lose the run."""
    try:
        sink(layer)
        return True
    except Exception:  # noqa: BLE001
        return False


def _error_result(message, elapsed, **extra):
    result = {
        "ok": False,
        "error": message,
        "errors": [message],
        "elapsed": elapsed,
    }
    result.update(extra)
    return result


def run_processing_algorithm_task(
    executor,
    alg_id,
    parameters=None,
    cancel=None,
    description=None,
    poll_interval=0.05,
    main_thread_timeout=None,
    result_layer_sink=None,
):
    """Run a Processing algorithm via ``QgsProcessingAlgRunnerTask``.

    ``executor`` must be a ``MainThreadExecutor`` created on the QGIS main
    thread. The ``QgsProcessingContext`` and ``QgsProcessingFeedback`` are kept
    referenced by both the task and the waiting slot for at least the task's
    lifetime.
    """
    if not isinstance(alg_id, str) or not alg_id.strip():
        return _error_result("alg_id must be a non-empty string", 0.0)

    start = time.perf_counter()
    task_description = description or f"AgenticGIS processing {alg_id}"
    params = dict(parameters or {})
    slot = {
        "done": threading.Event(),
        "result": None,
        "task": None,
        "context": None,
        "feedback": None,
        "cancel_requested": False,
    }

    def elapsed():
        return time.perf_counter() - start

    def executed(successful, results):
        if slot["cancel_requested"]:
            slot["result"] = _error_result(
                "cancelled by user",
                elapsed(),
                cancelled=True,
            )
        elif successful:
            collected = _take_result_layers(slot["context"], results, result_layer_sink)
            output = _stringify_results(results)
            output.update(collected)
            slot["result"] = {
                "ok": True,
                "results": output,
                "output": output,
                "elapsed": elapsed(),
            }
            if collected:
                slot["result"]["hint"] = (
                    "Output layer(s) are kept in memory. Pass the output id "
                    "directly as a param to another run_processing call, or "
                    "call add_layer(uri=<output id>, name=..., is_analysis=true) "
                    "to add it to the project. Vector outputs include a "
                    "*_feature_count — if the question only needs a count, "
                    "that is already the answer."
                )
        else:
            # Extract the actual QGIS error from feedback and task state so the
            # agent can self-correct instead of getting a generic message.
            feedback = slot.get("feedback")
            task = slot.get("task")
            errors = []
            if feedback is not None:
                try:
                    for msg in feedback.errors():
                        if msg:
                            errors.append(str(msg))
                except Exception:  # noqa: BLE001
                    pass
            if task is not None and hasattr(task, "exception"):
                try:
                    exc = task.exception()
                    if exc is not None:
                        errors.append(f"{type(exc).__name__}: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            error_msg = (
                "; ".join(errors)
                if errors
                else "processing algorithm failed (unknown reason — check QGIS Processing log)"
            )
            slot["result"] = _error_result(
                error_msg,
                elapsed(),
                results=_stringify_results(results),
            )
        slot["done"].set()

    def start_task():
        log_event("processing_task.start_task.begin", alg_id=alg_id)
        registry = QgsApplication.processingRegistry()
        algorithm = registry.createAlgorithmById(alg_id)
        if algorithm is None:
            raise ValueError(f"No processing algorithm found for id {alg_id!r}")

        context = QgsProcessingContext()
        if hasattr(context, "setProject"):
            context.setProject(QgsProject.instance())
        feedback = QgsProcessingFeedback()
        # Lazy default: avoid eager evaluation of QgsTask.CanCancel in case
        # it is removed in QGIS 4 (would crash before the getattr fallback).
        try:
            _default_flag = QgsTask.CanCancel
        except AttributeError:
            _default_flag = 0  # fallback when QGIS 4 removes the constant
        flags = getattr(QgsProcessingAlgRunnerTask, "CanCancel", _default_flag)
        task = QgsProcessingAlgRunnerTask(algorithm, params, context, feedback, flags)

        # PyQGIS callers must keep these alive for the whole task lifetime.
        task._agenticgis_algorithm = algorithm
        task._agenticgis_context = context
        task._agenticgis_feedback = feedback

        slot["task"] = task
        slot["context"] = context
        slot["feedback"] = feedback
        task.executed.connect(executed)
        QgsApplication.taskManager().addTask(task)
        log_event("processing_task.added", alg_id=alg_id, description=task_description)
        return task

    try:
        task = executor.run_sync(start_task, timeout=main_thread_timeout)
    except BaseException as exc:  # noqa: BLE001
        log_event(
            "processing_task.schedule.error",
            alg_id=alg_id,
            elapsed_ms=int(elapsed() * 1000),
            timeout=main_thread_timeout,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return _error_result(
            f"{type(exc).__name__}: {exc}",
            elapsed(),
            stage="schedule_task",
            main_thread_timeout=main_thread_timeout,
        )

    cancel_sent = False
    while not slot["done"].wait(poll_interval):
        if cancel_sent or not _cancel_requested(cancel):
            continue
        cancel_sent = True
        slot["cancel_requested"] = True

        def cancel_task():
            try:
                if slot["feedback"] is not None:
                    slot["feedback"].cancel()
                task.cancel()
            finally:
                log_event(
                    "processing_task.cancel",
                    alg_id=alg_id,
                    elapsed_ms=int(elapsed() * 1000),
                )

        try:
            executor.run_sync(cancel_task)
        except BaseException as exc:  # noqa: BLE001
            slot["result"] = _error_result(
                f"failed to cancel task: {type(exc).__name__}: {exc}",
                elapsed(),
                cancelled=True,
            )
            slot["done"].set()

    result = slot["result"] or _error_result(
        "processing task finished without a result",
        elapsed(),
    )
    log_event(
        "processing_task.done",
        alg_id=alg_id,
        elapsed_ms=int(result.get("elapsed", elapsed()) * 1000),
        ok=bool(result.get("ok")),
        cancelled=bool(result.get("cancelled")),
    )
    return result
