"""The single source of QGIS capability that all agent backends drive.

Every method here assumes it is running on the QGIS **main thread**. Callers
on worker threads must wrap invocations in ``MainThreadExecutor.run_sync``.
``run_pyqgis`` is the catch-all that gives the agent access to every QGIS
feature and every installed plugin; the other methods are convenience/
introspection helpers that keep common requests cheap and reliable.

Cancellation
------------
A lightweight ``CancellationRegistry`` lives on the toolkit. Every top-level
worker call wraps its dispatch in a token that the Stop button (or a
``QgsFeedback`` created by QGIS's processing framework) can set. Tokens are
re-entrant and torn down in ``finally`` so a tool that succeeds still clears
its slot.
"""

import io
import re
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import Qgis
from qgis.core import (
    QgsApplication,
    QgsFeatureRequest,
    QgsMapLayer,
    QgsProject,
    QgsTask,
    QgsVectorLayer,
    QgsVectorLayerCache,
)

from .cancellation import CancellationRegistry as _CancellationRegistry
from .analysis_cache import AnalysisCache, layer_cache_token
from .dev_logging import log_event
from .layer_analysis import analyze_vector_layer
from .processing_tasks import run_processing_algorithm_task


# --------------------------------------------------------------------------- #
# Cancellation                                                                 #
# --------------------------------------------------------------------------- #


def _make_qgs_feedback(event):
    """Build a ``QgsFeedback`` whose ``isCanceled`` mirrors ``event``.

    Every time the algorithm polls ``isCanceled`` we also pump the Qt event
    loop (throttled to ~50 ms) so the UI stays responsive during heavy
    geometry operations (clip, dissolve, buffer, …) that run on the main
    thread.  Falls back to ``None`` (and the caller does nothing) if QGIS
    isn't around.
    """
    if event is None:
        return None
    try:
        from qgis.core import QgsFeedback
    except Exception:  # pragma: no cover
        return None
    fb = QgsFeedback()
    _last_pump = [0.0]

    def _check_and_pump():
        now = time.monotonic()
        if now - _last_pump[0] >= 0.05:      # 50 ms throttle
            _last_pump[0] = now
            QCoreApplication.processEvents()
        return event.is_set()

    fb.isCanceled = _check_and_pump
    return fb


def _layer_brief(layer):
    # Guard every call: temporary/scratch layers can have a live Python wrapper
    # but a partially-freed C++ object, causing a segfault if accessed naively.
    try:
        info = {
            "id": layer.id(),
            "name": layer.name(),
            "type": "vector" if layer.type() == QgsMapLayer.VectorLayer else (
                "raster" if layer.type() == QgsMapLayer.RasterLayer else "other"
            ),
            "crs": layer.crs().authid() if layer.crs().isValid() else None,
            "valid": layer.isValid(),
        }
    except RuntimeError:
        return {"ok": False, "id": "?", "name": "?", "valid": False,
                "error": "layer no longer available"}

    if isinstance(layer, QgsVectorLayer):
        try:
            info["feature_count"] = layer.featureCount()
        except Exception:
            info["feature_count"] = -1
        try:
            info["geometry_type"] = layer.geometryType()  # 0=point,1=line,2=polygon
        except Exception:
            info["geometry_type"] = -1
        try:
            info["selected_count"] = layer.selectedFeatureCount()
        except Exception:
            info["selected_count"] = 0
    return info


def _no_geometry_flag():
    """Return the QGIS no-geometry feature request flag across QGIS versions."""
    try:
        return Qgis.FeatureRequestFlag.NoGeometry
    except AttributeError:
        return QgsFeatureRequest.NoGeometry


# Sentinel result for cancelled tool calls (a string so JSON-serialisable).
_CANCELLED_SENTINEL = "__cancelled__"
DEFAULT_FEATURE_SCAN_LIMIT = 100_000
EVENT_PUMP_INTERVAL = 100


def _cancel_requested(cancel):
    if cancel is None:
        return False
    if hasattr(cancel, "isCanceled"):
        return bool(cancel.isCanceled())
    if hasattr(cancel, "is_set"):
        return bool(cancel.is_set())
    return False


def _calculate_chart_for_layer(layer, field_name, chart_type="bar", cancel=None, pump_events=False):
    start = time.perf_counter()
    if layer is None:
        return {"ok": False, "error": "No layer provided"}
    if not isinstance(layer, QgsVectorLayer):
        return {"ok": False, "error": "Layer is not a vector layer"}

    field_idx = layer.fields().indexFromName(field_name)
    if field_idx == -1:
        return {"ok": False, "error": f"Field {field_name!r} not found"}

    req = QgsFeatureRequest().setFlags(_no_geometry_flag())
    req.setSubsetOfAttributes([field_name], layer.fields())

    values = {}
    scanned = 0
    for i, feature in enumerate(layer.getFeatures(req)):
        if _cancel_requested(cancel):
            return {"ok": False, "error": "cancelled by user", "cancelled": True}
        if i >= DEFAULT_FEATURE_SCAN_LIMIT:
            break
        attrs = feature.attributes()
        val = attrs[field_idx] if field_idx < len(attrs) else None
        values[val] = values.get(val, 0) + 1
        scanned = i + 1
        if i % EVENT_PUMP_INTERVAL == 0:
            if pump_events:
                QCoreApplication.processEvents()
            elif hasattr(cancel, "setProgress"):
                cancel.setProgress(min(99.0, (i / DEFAULT_FEATURE_SCAN_LIMIT) * 100.0))

    sorted_items = sorted(values.items(), key=lambda x: x[1], reverse=True)[:20]
    result = {
        "ok": True,
        "chart_type": chart_type,
        "title": f"{field_name} in {layer.name()}",
        "data": [{"label": str(k), "value": v} for k, v in sorted_items],
        "field": field_name,
        "layer_name": layer.name(),
        "scanned_features": scanned,
        "truncated": scanned >= DEFAULT_FEATURE_SCAN_LIMIT,
    }
    log_event(
        "layer.chart.scan",
        layer=result["layer_name"],
        field=field_name,
        scanned_features=scanned,
        truncated=result["truncated"],
        elapsed_ms=int((time.perf_counter() - start) * 1000),
    )
    return result


def _calculate_statistics_for_layer(layer, field_name=None, cancel=None, pump_events=False):
    start = time.perf_counter()
    if layer is None:
        return {"ok": False, "error": "No layer provided"}
    if not isinstance(layer, QgsVectorLayer):
        return {"ok": False, "error": "Layer is not a vector layer"}

    stats = {
        "layer_name": layer.name(),
        "total_features": layer.featureCount(),
        "valid": layer.isValid(),
        "crs": layer.crs().authid() if layer.crs().isValid() else None,
        "geometry_type": layer.geometryType(),
    }

    if not field_name:
        result = {"ok": True, "statistics": stats}
        log_event(
            "layer.statistics.scan",
            layer=stats["layer_name"],
            field=None,
            scanned_features=0,
            truncated=False,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
        )
        return result

    field_idx = layer.fields().indexFromName(field_name)
    if field_idx == -1:
        return {"ok": False, "error": f"Field {field_name!r} not found"}

    req = QgsFeatureRequest().setFlags(_no_geometry_flag())
    req.setSubsetOfAttributes([field_name], layer.fields())

    scanned = 0
    distinct_values = set()
    null_count = 0
    numeric_count = 0
    numeric_min = None
    numeric_max = None
    numeric_sum = 0.0
    numeric_sum_sq = 0.0
    for i, feature in enumerate(layer.getFeatures(req)):
        if _cancel_requested(cancel):
            return {"ok": False, "error": "cancelled by user", "cancelled": True}
        if i >= DEFAULT_FEATURE_SCAN_LIMIT:
            break
        attrs = feature.attributes()
        val = attrs[field_idx] if field_idx < len(attrs) else None
        scanned = i + 1
        distinct_values.add(val)
        if val is None:
            null_count += 1
        else:
            try:
                num = float(val)
            except (TypeError, ValueError):
                num = None
            if num is not None:
                numeric_count += 1
                numeric_sum += num
                numeric_sum_sq += num * num
                numeric_min = num if numeric_min is None else min(numeric_min, num)
                numeric_max = num if numeric_max is None else max(numeric_max, num)
        if i % EVENT_PUMP_INTERVAL == 0:
            if pump_events:
                QCoreApplication.processEvents()
            elif hasattr(cancel, "setProgress"):
                cancel.setProgress(min(99.0, (i / DEFAULT_FEATURE_SCAN_LIMIT) * 100.0))

    stats["field"] = field_name
    stats["distinct_count"] = len(distinct_values)
    stats["null_count"] = null_count
    stats["scanned_features"] = scanned
    stats["truncated"] = scanned >= DEFAULT_FEATURE_SCAN_LIMIT

    if numeric_count:
        stats["min"] = numeric_min
        stats["max"] = numeric_max
        stats["mean"] = numeric_sum / numeric_count
        stats["sum"] = numeric_sum
        stats["count"] = numeric_count
        if numeric_count > 1:
            variance = (
                numeric_sum_sq - ((numeric_sum * numeric_sum) / numeric_count)
            ) / (numeric_count - 1)
            stats["stdev"] = max(variance, 0.0) ** 0.5
        else:
            stats["stdev"] = 0.0

    result = {"ok": True, "statistics": stats}
    log_event(
        "layer.statistics.scan",
        layer=stats["layer_name"],
        field=field_name,
        scanned_features=stats.get("scanned_features", 0),
        truncated=bool(stats.get("truncated")),
        elapsed_ms=int((time.perf_counter() - start) * 1000),
    )
    return result


class QgisToolkit:
    """Capability surface exposed to the agent. Construct on the main thread."""

    # Bound by the agent loop / dock on construction.
    should_stop_fn = None  # type: callable | None  # threaded "stop requested?"

    def __init__(self, iface, config=None):
        self.iface = iface
        self.config = config
        self._alg_cache = None  # caches full algorithm list
        self._cancel = _CancellationRegistry()
        self._ask_emitter = None
        self._ask_user_lock = threading.Lock()
        self._ask_user_pending = None
        self._bg_task_lock = threading.Lock()
        self._bg_tasks = set()
        self._analysis_cache = AnalysisCache()
        self._ns_template = None  # F10: cached exec namespace
        # F17: dirty flag — set when a tool may have mutated project state.
        self._canvas_dirty = False
        # F8: hook QGIS's plugins-changed signal so the algorithm list
        # reflects newly-enabled providers (GRASS, SAGA, custom plugins)
        # without a plugin restart.
        try:
            from qgis.core import QgsApplication
            QgsApplication.pluginsChanged.connect(self._invalidate_alg_cache)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Clarifying-question flow (ask_user tool)                            #
    # ------------------------------------------------------------------ #
    def set_ask_user_emitter(self, emitter):
        """Register a callback that asks the user a clarifying question.

        The emitter signature is ``emitter(question, options, allow_free_text)``.
        It is expected to be non-blocking; it fires ``self._resolve_ask_user(payload)``
        from the main thread when the user replies (or the dock is cleared).
        """
        self._ask_emitter = emitter

    def ask_user(self, question, options, allow_free_text=True):
        """Toolkit implementation of the ``ask_user`` tool.

        Return contract (deliberately different from the other toolkit
        methods, which wrap returns in ``{"ok": ...}``):

        - Success: a raw dict ``{"choice": str|None, "free_text": str|None, "cancelled": bool}``.
        - Validation failure (bad options / question, or recursive call): a
          plain string starting with ``"ask_user: "`` describing the error.
          The backends do not set ``is_error`` on these — schema validation
          is a separate concern from operation outcome.
        """
        if not isinstance(question, str) or not question.strip():
            log_event("ask_user.validation_error", reason="empty_question")
            return "ask_user: question must be a non-empty string"

        options = self._normalize_ask_user_options(options)
        if len(options) < 2:
            log_event(
                "ask_user.validation_error",
                reason="not_enough_options",
                option_count=len(options),
            )
            return f"ask_user: options must have 2-4 items, got {len(options)}"
        if len(options) > 4:
            options = options[:4]

        if self._ask_emitter is None:
            return {"choice": None, "free_text": None, "cancelled": True}

        # Recursive guard: only one ask_user at a time
        with self._ask_user_lock:
            if self._ask_user_pending is not None:
                return "ask_user: already waiting for user input"

            wait = threading.Event()
            self._ask_user_pending = (wait, {"choice": None, "free_text": None, "cancelled": False})

        try:
            if self._ask_emitter is not None:
                self._ask_emitter(question, list(options), bool(allow_free_text))

            # Wait for the dock (or a test) to fire _resolve_ask_user.
            wait_evt, _ = self._ask_user_pending
            wait_evt.wait()
        finally:
            with self._ask_user_lock:
                payload = (
                    self._ask_user_pending[1]
                    if self._ask_user_pending is not None
                    else {"choice": None, "free_text": None, "cancelled": True}
                )
                self._ask_user_pending = None
        return payload

    @staticmethod
    def _normalize_ask_user_options(options):
        """Accept common model-emitted option shapes and return label dicts."""
        normalized = []
        if isinstance(options, dict):
            options = [
                {"label": str(key), "description": str(value) if value is not None else ""}
                for key, value in options.items()
            ]
        if not isinstance(options, (list, tuple)):
            return normalized

        for item in options:
            if isinstance(item, str):
                label = item.strip()
                desc = ""
            elif isinstance(item, dict):
                raw_label = (
                    item.get("label")
                    or item.get("title")
                    or item.get("name")
                    or item.get("value")
                    or item.get("choice")
                )
                label = str(raw_label).strip() if raw_label is not None else ""
                raw_desc = item.get("description") or item.get("detail") or item.get("help") or ""
                desc = str(raw_desc).strip() if raw_desc is not None else ""
            else:
                label = str(item).strip() if item is not None else ""
                desc = ""
            if not label:
                continue
            normalized.append({"label": label, "description": desc})

        if len(normalized) == 1:
            normalized.append({
                "label": "Cancel",
                "description": "Stop this question and do not continue with that operation.",
            })
        return normalized[:4]

    def _resolve_ask_user(self, payload):
        """Called by the dock (or a test) to unblock ask_user.

        ``payload`` is a dict with keys ``choice``, ``free_text``, ``cancelled``.
        Missing keys default to None / False.
        """
        with self._ask_user_lock:
            if self._ask_user_pending is None:
                return  # nothing to resolve (stale fire, e.g. after Clear)
            wait_evt, slot = self._ask_user_pending
            slot["choice"] = payload.get("choice")
            slot["free_text"] = payload.get("free_text")
            slot["cancelled"] = bool(payload.get("cancelled", False))
            wait_evt.set()

    # ------------------------------------------------------------------ #
    # External access guardrails                                         #
    # ------------------------------------------------------------------ #
    _PATH_RE = re.compile(r"(^/|^~[/\\]|^\.[/\\]|^\.\.[/\\]|^[A-Za-z]:[/\\])")
    _FILE_SUFFIX_RE = re.compile(
        r"\.(shp|gpkg|geojson|json|csv|tsv|xlsx?|kml|kmz|tif|tiff|vrt|qgz|qgs|sqlite|db)(?:$|[?#])",
        re.IGNORECASE,
    )
    _EXTERNAL_CODE_MARKERS = (
        "open(",
        "Path(",
        "pathlib",
        "os.listdir(",
        "os.scandir(",
        "os.walk(",
        "glob.glob(",
        "QgsProject.instance().read(",
        "urllib",
        "requests",
        "socket.",
        "pandas.read_",
        "geopandas.read_",
    )
    _STRING_LITERAL_RE = re.compile(
        r"""(?P<quote>['"])(?P<value>(?:\\.|(?!\1).)*)(?P=quote)""",
        re.DOTALL,
    )

    def _known_layer_ids(self):
        try:
            return set(QgsProject.instance().mapLayers().keys())
        except Exception:
            return set()

    def _looks_external_reference(self, value):
        if not isinstance(value, str):
            return False
        text = value.strip()
        if not text:
            return False
        if text in self._known_layer_ids():
            return False
        lowered = text.lower()
        if lowered in ("memory:", "temporary_output", "temp", "scratch"):
            return False
        if lowered.startswith(("memory:", "scratch:", "qgis:")):
            return False
        if "://" in lowered:
            return True
        if self._PATH_RE.search(text):
            return True
        return bool(self._FILE_SUFFIX_RE.search(text))

    def _iter_strings(self, value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from self._iter_strings(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from self._iter_strings(item)

    def _external_access_reason(self, tool_name, args):
        args = dict(args or {})
        if tool_name == "add_layer" and self._looks_external_reference(args.get("uri")):
            return f"load external layer source: {args.get('uri')}"

        if tool_name == "run_processing":
            params = args.get("params") or {}
            for text in self._iter_strings(params):
                if self._looks_external_reference(text):
                    return f"use external processing path or URI: {text}"

        if tool_name == "run_pyqgis":
            code = args.get("code") or ""
            if (
                "ALLOW_EXTERNAL_ACCESS = True" in code
                or "ALLOW_EXTERNAL_ACCESS=True" in code
            ):
                return None
            for match in self._STRING_LITERAL_RE.finditer(code):
                value = match.group("value")
                if self._looks_external_reference(value):
                    return f"run PyQGIS code that references external path or URI: {value}"
            for marker in self._EXTERNAL_CODE_MARKERS:
                if marker in code:
                    return f"run PyQGIS code that may access files, URLs, or sources outside loaded layers ({marker})"

        return None

    def confirm_external_access(self, tool_name, args):
        """Return None when allowed, otherwise a tool-style error result.

        External file/path/URL access is allowed only after the user confirms
        through one controlled permission popup. Returning ``None`` means
        allowed; returning a dict blocks the original tool call.
        """
        reason = self._external_access_reason(tool_name, args)
        if not reason:
            return None

        answer = self.ask_user(
            f"Allow AgenticGIS to {reason}?",
            [
                {
                    "label": "Allow once",
                    "description": "Permit this operation, then ask again next time.",
                },
                {
                    "label": "Deny",
                    "description": "Block this operation and keep analysis inside loaded layers.",
                },
            ],
            allow_free_text=False,
        )
        if isinstance(answer, str):
            log_event("external_access.permission_error", tool=tool_name, error=answer)
            return {"ok": False, "error": answer, "cancelled": True}
        choice = (answer or {}).get("choice")
        if choice == "Allow once":
            log_event("external_access.allowed", tool=tool_name, reason=reason)
            return None
        log_event("external_access.denied", tool=tool_name, reason=reason, choice=choice)
        return {
            "ok": False,
            "error": f"Permission denied: {reason}",
            "cancelled": True,
        }

    # ------------------------------------------------------------------ #
    # Cancellation helpers                                                #
    # ------------------------------------------------------------------ #
    def request_cancel(self):
        """Called by the dock's Stop button. Flips the active token."""
        self._cancel.cancel()
        log_event("toolkit.cancel.requested")

    def is_cancelled(self):
        return self._cancel.is_cancelled()

    # ------------------------------------------------------------------ #
    # Background read-only vector analysis                               #
    # ------------------------------------------------------------------ #
    _BACKGROUND_TOOLS = {"analyze_layer", "create_chart", "get_layer_statistics"}

    def _ensure_background_task_state(self):
        """Initialize background-task fields for live dev-reloaded instances."""
        if not hasattr(self, "_bg_task_lock"):
            self._bg_task_lock = threading.Lock()
        if not hasattr(self, "_bg_tasks"):
            self._bg_tasks = set()

    def _ensure_analysis_cache(self):
        if not hasattr(self, "_analysis_cache"):
            self._analysis_cache = AnalysisCache()
        return self._analysis_cache

    def _analysis_cache_key(self, layer, args):
        fields = args.get("fields")
        if fields is None and args.get("field_name"):
            fields = [args.get("field_name")]
        fields_key = tuple(fields or ())
        return (
            layer_cache_token(layer),
            args.get("analysis_type", "auto"),
            fields_key,
            int(args.get("sample_limit", 5) or 0),
            int(args.get("scan_limit", DEFAULT_FEATURE_SCAN_LIMIT) or 0),
            int(args.get("top_limit", 10) or 0),
        )

    def can_run_background(self, name):
        return name in self._BACKGROUND_TOOLS

    def run_background_tool(self, executor, name, args):
        """Run supported read-only layer analysis using QgsTask.

        The live project layer is inspected on the main thread, then the
        background task opens its own read-only QgsVectorLayer from the same
        source URI. Memory/scratch layers fall back to the main-thread path
        because they cannot be safely reopened in a task.
        """
        args = dict(args or {})
        layer_id = args.get("layer_id")
        start = time.perf_counter()
        self._ensure_background_task_state()
        log_event("background_tool.start", tool=name, layer_id=layer_id)

        def snapshot():
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is None:
                return {"error": {"ok": False, "error": f"No layer with id {layer_id!r}"}}
            if not isinstance(layer, QgsVectorLayer):
                return {"error": {"ok": False, "error": "Layer is not a vector layer"}}
            provider = layer.providerType()
            source = layer.source()
            if not source or provider == "memory":
                return {"fallback": True}
            return {
                "source": source,
                "provider": provider,
                "name": layer.name(),
                "cache_key": self._analysis_cache_key(layer, args)
                if name == "analyze_layer" else None,
            }

        snap = executor.run_sync(snapshot)
        if snap.get("error") is not None:
            log_event(
                "background_tool.end",
                tool=name,
                path="snapshot_error",
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                ok=False,
            )
            return snap["error"]
        if snap.get("fallback"):
            result = executor.run_sync(lambda: getattr(self, name)(**args))
            log_event(
                "background_tool.end",
                tool=name,
                path="fallback_main_thread",
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                ok=bool(result.get("ok")) if isinstance(result, dict) else None,
            )
            return result

        def worker(task):
            layer = QgsVectorLayer(snap["source"], snap["name"], snap["provider"])
            if not layer.isValid():
                return executor.run_sync(lambda: getattr(self, name)(**args))
            if name == "analyze_layer":
                cache = self._ensure_analysis_cache()
                cached = cache.get(snap["cache_key"])
                if cached is not None:
                    cached["cached"] = True
                    return cached
                result = self._analyze_layer_object(layer, args, feedback=task)
                if isinstance(result, dict) and result.get("ok"):
                    cache.set(snap["cache_key"], result)
                return result
            if name == "create_chart":
                return _calculate_chart_for_layer(
                    layer,
                    args.get("field_name"),
                    args.get("chart_type", "bar"),
                    cancel=task,
                    pump_events=False,
                )
            return _calculate_statistics_for_layer(
                layer,
                args.get("field_name"),
                cancel=task,
                pump_events=False,
            )

        result = self._run_qgs_task(executor, f"AgenticGIS {name}", worker)
        log_event(
            "background_tool.end",
            tool=name,
            path="qgs_task",
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            ok=bool(result.get("ok")) if isinstance(result, dict) else None,
            cancelled=bool(result.get("cancelled")) if isinstance(result, dict) else False,
        )
        return result

    def _run_qgs_task(self, executor, description, worker):
        self._ensure_background_task_state()
        slot = {"done": threading.Event(), "result": None, "error": None, "task": None}

        def finished(exception, value=None):
            slot["error"] = exception
            slot["result"] = value
            slot["done"].set()

        def start_task():
            task = QgsTask.fromFunction(description, worker, on_finished=finished)
            slot["task"] = task
            with self._bg_task_lock:
                self._bg_tasks.add(task)
            QgsApplication.taskManager().addTask(task)
            log_event("qgs_task.added", description=description)
            return task

        task = executor.run_sync(start_task)
        start = time.perf_counter()
        cancel_sent = False
        try:
            while not slot["done"].wait(0.05):
                if cancel_sent or not self.is_cancelled():
                    continue

                cancel_sent = True

                def cancel_task():
                    try:
                        task.cancel()
                    finally:
                        log_event(
                            "qgs_task.cancel",
                            description=description,
                            elapsed_ms=int((time.perf_counter() - start) * 1000),
                        )

                try:
                    executor.run_sync(cancel_task)
                except Exception as exc:
                    log_event(
                        "qgs_task.cancel.error",
                        description=description,
                        elapsed_ms=int((time.perf_counter() - start) * 1000),
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
            if slot["error"] is not None:
                log_event(
                    "qgs_task.error",
                    description=description,
                    elapsed_ms=int((time.perf_counter() - start) * 1000),
                    error_type=type(slot["error"]).__name__,
                )
                return {
                    "ok": False,
                    "error": f"{type(slot['error']).__name__}: {slot['error']}",
                }
            log_event(
                "qgs_task.done",
                description=description,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
            return slot["result"]
        finally:
            with self._bg_task_lock:
                self._bg_tasks.discard(task)

    # F16: list of (module_path, attribute) tuples we treat as destructive.
    # Code that imports any of these is refused when the safety flag is on.
    _DANGEROUS_SYMBOLS = (
        ("os", "system"),
        ("os", "remove"),
        ("os", "unlink"),
        ("os", "rmdir"),
        ("shutil", "rmtree"),
        ("subprocess", "Popen"),
        ("subprocess", "call"),
        ("subprocess", "run"),
        ("subprocess", "check_output"),
        ("ctypes", "CDLL"),
        ("ctypes", "WinDLL"),
    )

    def _dangerous_calls_blocked(self, code, ns):
        """Return True when the safety flag is on and the code uses a
        destructive builtin *and* does not set ``ALLOW_DANGEROUS = True``.

        We do a cheap string-level check rather than static analysis — the
        goal is to catch the obvious cases ("the agent tried to run rm"),
        not to be a sandbox. Anything that bypasses the string check is
        still subject to the user's existing OS-level permissions.
        """
        if not (self.config and self.config.get("confirm_dangerous_calls")):
            return False
        if "ALLOW_DANGEROUS = True" in code or "ALLOW_DANGEROUS=True" in code:
            return False
        lowered = code  # keep case — the dangerous names are lower-case already
        for mod, attr in self._DANGEROUS_SYMBOLS:
            # Look for ``mod.attr(`` or ``mod . attr(`` — the form that
            # actually invokes the function.
            needle = f"{mod}.{attr}("
            if needle in lowered:
                return True
        return False

    # ------------------------------------------------------------------ #
    # The catch-all: arbitrary PyQGIS execution                          #
    # ------------------------------------------------------------------ #
    def run_pyqgis(self, code):
        """Execute arbitrary PyQGIS ``code`` and return captured output.

        The execution namespace pre-binds the names a PyQGIS user expects:
        ``iface``, ``QgsProject``, ``qgis`` (core/gui), ``processing`` and the
        ``QgsApplication``. Assign to a variable named ``result`` to return a
        structured value to the agent. ``stdout``/``stderr`` are captured.

        A ``_cancel_check()`` callable is also injected; user code that calls
        it periodically can be interrupted by the Stop button. ``time.sleep``
        is wrapped to honour the same flag so a sleeping agent loop yields
        within a few hundred milliseconds.
        """
        start = time.perf_counter()
        log_event("toolkit.run_pyqgis.start", code_len=len(code) if isinstance(code, str) else None)
        event, owner = self._cancel.register()
        try:
            result = self._run_pyqgis_inner(code, event, owner)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            log_event(
                "toolkit.run_pyqgis.end",
                elapsed_ms=elapsed_ms,
                ok=bool(result.get("ok")) if isinstance(result, dict) else None,
                cancelled=bool(result.get("cancelled")) if isinstance(result, dict) else False,
            )
            if elapsed_ms > 5000 and isinstance(result, dict):
                result["slow_ms"] = elapsed_ms
                result.setdefault("hint", (
                    f"This call took {elapsed_ms // 1000}s on the QGIS main thread. "
                    "For field stats use get_layer_statistics; for summaries use analyze_layer."
                ))
            return result
        finally:
            self._cancel.release(event)

    def _run_pyqgis_inner(self, code, event, owner):
        import qgis.core as qgis_core
        import qgis.gui as qgis_gui

        # F10: namespace is built once per toolkit and copied per call.
        if self._ns_template is None:
            ns = {
                "__name__": "__agenticgis__",
                "iface": self.iface,
                "QgsProject": QgsProject,
                "QgsApplication": QgsApplication,
                "qgis": __import__("qgis"),
                "qgis_core": qgis_core,
                "qgis_gui": qgis_gui,
            }
            ns.update({k: getattr(qgis_core, k) for k in dir(qgis_core) if not k.startswith("_")})
            try:
                import processing  # noqa: WPS433 (optional at import time)
                ns["processing"] = processing
            except Exception:  # pragma: no cover - processing should exist in QGIS
                pass
            self._ns_template = ns
        ns = dict(self._ns_template)  # shallow copy — per-call top-level

        # Cancellation hooks injected into the agent's namespace.
        def _cancel_check():
            return event is not None and event.is_set()
        ns["_cancel_check"] = _cancel_check
        ns["is_cancelled"] = _cancel_check

        # Wrap time.sleep so user code can't stall the loop on a long sleep
        # without being interruptible. The wrapper wakes every 200ms to
        # honour a cancel flag; small sleeps stay reasonably accurate.
        _real_sleep = time.sleep

        def _interruptible_sleep(seconds):
            end = time.monotonic() + max(0.0, float(seconds))
            while True:
                if _cancel_check():
                    return
                remaining = end - time.monotonic()
                if remaining <= 0:
                    return
                _real_sleep(min(remaining, 0.2))

        ns["time"] = type("t", (), {
            "sleep": _interruptible_sleep,
            "monotonic": time.monotonic,
            "time": time.time,
        })()

        # Performance helper: efficient feature iteration without materialising
        # large layers into memory. Agent code should iterate this generator or
        # use _sample_features for previews.
        def _iterate_features(layer, fields=None, no_geometry=False, limit=None):
            """Yield layer features efficiently using ``QgsFeatureRequest``."""
            if not isinstance(layer, QgsVectorLayer):
                return
            req = QgsFeatureRequest()
            if no_geometry:
                req.setFlags(_no_geometry_flag())
            if fields:
                req.setSubsetOfAttributes(fields, layer.fields())
            if limit is not None:
                req.setLimit(int(limit))
            for i, feature in enumerate(layer.getFeatures(req)):
                if _cancel_check():
                    break
                if i % EVENT_PUMP_INTERVAL == 0:
                    QCoreApplication.processEvents()
                yield feature

        ns["_iterate_features"] = _iterate_features
        ns["QgsFeatureRequest"] = QgsFeatureRequest

        def _sample_features(layer, limit=100, fields=None, no_geometry=True):
            """Return a bounded preview list; safe for large layers."""
            return list(_iterate_features(
                layer,
                fields=fields,
                no_geometry=no_geometry,
                limit=limit,
            ))

        ns["_sample_features"] = _sample_features

        # Layer cache helper for repeated efficient access
        def _make_layer_cache(layer_id, cache_size=10000):
            layer = QgsProject.instance().mapLayer(layer_id)
            if not isinstance(layer, QgsVectorLayer):
                return None
            cache = QgsVectorLayerCache(layer, cache_size)
            cache.setFullCache(True)
            return cache

        ns["_make_layer_cache"] = _make_layer_cache
        ns["QgsVectorLayerCache"] = QgsVectorLayerCache

        out, err = io.StringIO(), io.StringIO()
        result = {"ok": True, "stdout": "", "stderr": "", "result": None, "error": None}
        if not isinstance(code, str) or not code.strip():
            return {"ok": False, "error": "run_pyqgis: code must be a non-empty string", "stdout": "", "stderr": ""}
        if len(code) > 200_000:
            return {"ok": False, "error": "run_pyqgis: code is too large (>200k chars)", "stdout": "", "stderr": ""}
        # F16: optional guard against destructive builtins. The flag is
        # opt-in so the existing "zero friction" behaviour is preserved by
        # default; users who want a safety net flip it on in Settings.
        if self._dangerous_calls_blocked(code, ns):
            return {"ok": False,
                    "error": ("run_pyqgis: code references a destructive "
                              "builtin (os.system, subprocess, shutil.rmtree, "
                              "ctypes, ...). Set 'Allow dangerous calls' in "
                              "Settings or define ALLOW_DANGEROUS = True at "
                              "the top of the code to override."),
                    "stdout": "", "stderr": ""}
        try:
            with redirect_stdout(out), redirect_stderr(err):
                exec(compile(code, "<agenticgis>", "exec"), ns)  # noqa: S102
            if _cancel_check():
                result["ok"] = False
                result["error"] = "run_pyqgis: cancelled by user"
                result["cancelled"] = True
            elif "result" in ns:
                result["result"] = repr(ns["result"])
        except KeyboardInterrupt:
            return {"ok": False, "error": "run_pyqgis: interrupted by user", "stdout": out.getvalue(), "stderr": err.getvalue()}
        except SystemExit:
            return {"ok": False, "error": "run_pyqgis: code called sys.exit()", "stdout": out.getvalue(), "stderr": err.getvalue()}
        except BaseException as exc:  # noqa: BLE001 — report back to the agent, never crash QGIS
            result["ok"] = False
            result["error"] = traceback.format_exc()
        finally:
            result["stdout"] = out.getvalue()
            result["stderr"] = err.getvalue()
        # F17: only refresh if the agent's code may have touched the canvas.
        if self._canvas_dirty:
            try:
                self.iface.mapCanvas().refresh()
            except Exception:
                pass
            self._canvas_dirty = False
        return result

    # ------------------------------------------------------------------ #
    # Introspection helpers                                              #
    # ------------------------------------------------------------------ #
    def get_project_state(self, **_):
        project = QgsProject.instance()
        canvas = self.iface.mapCanvas()
        active = self.iface.activeLayer()
        extent = canvas.extent()
        return {
            "ok": True,
            "project_path": project.fileName() or None,
            "title": project.title() or None,
            "crs": project.crs().authid() if project.crs().isValid() else None,
            "layer_count": len(project.mapLayers()),
            "active_layer_id": active.id() if active else None,
            "active_layer_name": active.name() if active else None,
            "canvas_extent": [extent.xMinimum(), extent.yMinimum(),
                              extent.xMaximum(), extent.yMaximum()],
            "layers": [_layer_brief(layer) for layer in project.mapLayers().values()],
        }

    def list_layers(self, limit=None, offset=0):
        project = QgsProject.instance()
        layers = list(project.mapLayers().values())
        total = len(layers)
        start = offset or 0
        end = (start + limit) if limit else total
        sliced = layers[start:end]
        result = [_layer_brief(layer) for layer in sliced]
        # Backward compatibility: return raw list when no pagination requested
        if limit is None and not offset:
            return result
        return {
            "ok": True,
            "total": total,
            "limit": limit,
            "offset": start,
            "layers": result,
        }

    def get_layer_fields(self, layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"ok": False, "error": f"Layer {layer.name()!r} is not a vector layer"}
        return {
            "ok": True,
            "layer": layer.name(),
            "fields": [
                {"name": f.name(), "type": f.typeName(), "length": f.length()}
                for f in layer.fields()
            ],
        }

    def get_layer_summary(self, layer_id):
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        summary = _layer_brief(layer)
        summary["source"] = layer.publicSource()
        if isinstance(layer, QgsVectorLayer):
            extent = layer.extent()
            summary["extent"] = [extent.xMinimum(), extent.yMinimum(),
                                 extent.xMaximum(), extent.yMaximum()]
            summary["fields"] = [f.name() for f in layer.fields()]
        return summary

    def _analyze_layer_object(self, layer, args, feedback=None):
        fields = args.get("fields")
        field_name = args.get("field_name")
        if fields is None and field_name:
            fields = [field_name]
        if isinstance(fields, str):
            fields = [fields]

        scan_limit = int(args.get("scan_limit") or DEFAULT_FEATURE_SCAN_LIMIT)
        sample_limit = int(args.get("sample_limit") or 5)
        top_limit = int(args.get("top_limit") or 10)
        analysis_type = args.get("analysis_type") or "auto"

        result = analyze_vector_layer(
            layer,
            fields=fields,
            sample_limit=sample_limit,
            scan_limit=scan_limit,
            top_limit=top_limit,
            feedback=feedback,
        )
        payload = {
            "ok": not bool(result.get("canceled")),
            "analysis_type": analysis_type,
            "summary": result.get("summary"),
            "scanned_features": result.get("scanned_features", 0),
            "truncated": bool(result.get("truncated")),
            "scan_limit": result.get("scan_limit"),
            "cached": False,
        }
        if result.get("canceled"):
            payload.update({"error": "cancelled by user", "cancelled": True})
            return payload

        if analysis_type in ("auto", "summary"):
            payload["summary"] = result.get("summary")
        if analysis_type in ("auto", "field_stats"):
            payload["field_stats"] = result.get("field_stats", {})
        if analysis_type in ("auto", "category_counts", "top_values"):
            payload["category_counts"] = result.get("category_counts", {})
            payload["top_values"] = result.get("top_values", {})
        if analysis_type in ("auto", "sample"):
            payload["sample"] = result.get("sample", [])
        if analysis_type in ("auto", "missing_values"):
            payload["missing_values"] = result.get("missing_values", {})
        return payload

    def analyze_layer(
        self,
        layer_id,
        analysis_type="auto",
        fields=None,
        field_name=None,
        sample_limit=5,
        scan_limit=DEFAULT_FEATURE_SCAN_LIMIT,
        top_limit=10,
    ):
        """Structured bounded analysis for vector layers.

        This is the preferred tool for exploratory layer analysis because it
        centralizes large-layer safety instead of relying on generated PyQGIS.
        """
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"ok": False, "error": f"Layer {layer.name()!r} is not a vector layer"}

        args = {
            "analysis_type": analysis_type or "auto",
            "fields": fields,
            "field_name": field_name,
            "sample_limit": sample_limit,
            "scan_limit": scan_limit,
            "top_limit": top_limit,
        }
        cache = self._ensure_analysis_cache()
        key = self._analysis_cache_key(layer, args)
        cached = cache.get(key)
        if cached is not None:
            cached["cached"] = True
            return cached

        event, owner = self._cancel.register()
        try:
            result = self._analyze_layer_object(layer, args, feedback=event)
        finally:
            if owner:
                self._cancel.release(event)
        if isinstance(result, dict) and result.get("ok"):
            cache.set(key, result)
        return result

    def list_plugins(self):
        """List installed and active QGIS plugins so the agent knows what is
        available to drive (e.g. via ``run_pyqgis`` or their algorithms)."""
        try:
            from qgis.utils import active_plugins, available_plugins, plugins
        except Exception:
            return {"ok": False, "error": "qgis.utils not available"}
        return {
            "ok": True,
            "active": sorted(active_plugins),
            "available": sorted(available_plugins),
            "loaded": sorted(plugins.keys()),
        }

    # ------------------------------------------------------------------ #
    # Processing framework                                               #
    # ------------------------------------------------------------------ #
    def list_processing_algorithms(self, filter_text=""):
        if self._alg_cache is None:
            self._alg_cache = [
                {"id": alg.id(), "name": alg.displayName()}
                for alg in QgsApplication.processingRegistry().algorithms()
            ]
        algs = self._alg_cache
        needle = (filter_text or "").lower()
        if needle:
            algs = [a for a in algs if needle in a["id"].lower() or needle in a["name"].lower()]
        return {"ok": True, "count": len(algs), "algorithms": algs}

    def _invalidate_alg_cache(self):
        """Invalidate the processing algorithm cache (call after plugin changes)."""
        self._alg_cache = None

    def run_processing(self, alg_id, params):
        import processing

        if not isinstance(alg_id, str) or not alg_id.strip():
            return {"ok": False, "error": "alg_id must be a non-empty string"}
        start = time.perf_counter()
        param_keys = sorted((params or {}).keys()) if isinstance(params, dict) else []
        log_event("toolkit.run_processing.start", alg_id=alg_id, param_keys=param_keys)
        # F7: wire a feedback so the QGIS processing framework honours our
        # cancellation token. Falls back to a direct call if the framework
        # doesn't accept ``feedback`` (older QGIS).
        event, owner = self._cancel.register()
        feedback = _make_qgs_feedback(event) if owner else None

        try:
            if feedback is not None:
                try:
                    output = processing.run(alg_id, dict(params or {}), feedback=feedback)
                except TypeError:
                    # Older QGIS signature without feedback kwarg
                    output = processing.run(alg_id, dict(params or {}))
            else:
                output = processing.run(alg_id, dict(params or {}))
        except KeyboardInterrupt:
            # If the user cancelled, surface the cancel flag; otherwise
            # treat the interrupt as a generic error so the agent knows
            # it isn't something it can retry.
            if event is not None and event.is_set():
                result = {"ok": False, "error": "cancelled by user", "cancelled": True}
            else:
                result = {"ok": False, "error": "interrupted by user"}
            log_event(
                "toolkit.run_processing.end",
                alg_id=alg_id,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                ok=False,
                cancelled=bool(result.get("cancelled")),
            )
            return result
        except BaseException as exc:  # noqa: BLE001
            # F7: distinguish the cancel path from real errors so the agent
            # can decide whether to retry.
            if event is not None and event.is_set():
                result = {"ok": False, "error": "cancelled by user", "cancelled": True}
                log_event(
                    "toolkit.run_processing.end",
                    alg_id=alg_id,
                    elapsed_ms=int((time.perf_counter() - start) * 1000),
                    ok=False,
                    cancelled=True,
                )
                return result
            name = type(exc).__name__
            # Pull a "cleaner" message out of QGIS-specific exception types
            # when available.
            msg = str(exc)
            result = {"ok": False, "error": f"{name}: {msg}"}
            log_event(
                "toolkit.run_processing.end",
                alg_id=alg_id,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                ok=False,
                cancelled=False,
                error_type=name,
            )
            return result
        finally:
            self._cancel.release(event)

        # F17: processing likely mutated the canvas; mark dirty for the dock.
        self._canvas_dirty = True
        # Outputs may contain layers / non-serialisable objects; stringify.
        try:
            result = {"ok": True, "output": {k: str(v) for k, v in (output or {}).items()}}
        except BaseException as exc:  # noqa: BLE001
            result = {"ok": False, "error": f"failed to serialize output: {type(exc).__name__}: {exc}"}
        log_event(
            "toolkit.run_processing.end",
            alg_id=alg_id,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            ok=bool(result.get("ok")),
            cancelled=bool(result.get("cancelled")),
        )
        return result

    def run_processing_background(self, executor, alg_id, params):
        """Run a Processing algorithm through QgsProcessingAlgRunnerTask."""
        if not isinstance(alg_id, str) or not alg_id.strip():
            return {"ok": False, "error": "alg_id must be a non-empty string"}

        start = time.perf_counter()
        param_keys = sorted((params or {}).keys()) if isinstance(params, dict) else []
        timeout = 0.0
        if self.config is not None:
            try:
                timeout = self.config.get("processing_timeout", timeout)
            except Exception:
                timeout = 0.0
        task_setup_timeout = None
        log_event(
            "toolkit.run_processing_task.start",
            alg_id=alg_id,
            param_keys=param_keys,
            timeout=timeout,
            task_setup_timeout=task_setup_timeout,
        )

        event, owner = self._cancel.register()
        try:
            result = run_processing_algorithm_task(
                executor,
                alg_id,
                parameters=dict(params or {}),
                cancel=event,
                main_thread_timeout=task_setup_timeout,
            )
        finally:
            if owner:
                self._cancel.release(event)

        if isinstance(result, dict) and result.get("ok"):
            self._canvas_dirty = True
        log_event(
            "toolkit.run_processing_task.end",
            alg_id=alg_id,
            elapsed_ms=int((time.perf_counter() - start) * 1000),
            ok=bool(result.get("ok")) if isinstance(result, dict) else None,
            cancelled=bool(result.get("cancelled")) if isinstance(result, dict) else False,
        )
        return result

    # ------------------------------------------------------------------ #
    # Project mutation helpers                                           #
    # ------------------------------------------------------------------ #
    def add_layer(self, uri, name=None, provider="ogr"):
        name = name or uri.split("/")[-1]
        if provider in ("gdal", "raster"):
            from qgis.core import QgsRasterLayer
            layer = QgsRasterLayer(uri, name)
        else:
            layer = QgsVectorLayer(uri, name, provider)
        if not layer.isValid():
            return {"ok": False, "error": f"Layer is not valid: {uri!r}"}
        QgsProject.instance().addMapLayer(layer)
        self._canvas_dirty = True
        return {"ok": True, "layer_id": layer.id(), "name": layer.name()}

    def save_project(self):
        ok = QgsProject.instance().write()
        return {"ok": bool(ok), "path": QgsProject.instance().fileName() or None}

    def create_chart(self, layer_id, field_name, chart_type="bar", colors=None):
        """Generate chart data from a vector layer field.

        Returns structured data for the chat dock to render as a chart.

        Optional ``colors`` is a list of hex strings applied to the
        data points in display order. The chart widget cycles the
        list if it has fewer entries than data points, and falls back
        to its default grayscale palette when None or empty.
        """
        # Validate colors up front so the user gets a clear error
        # rather than a silent fallback. We accept both '#rrggbb' and
        # '#RGB' forms; case-insensitive.
        clean_colors = []
        if colors is not None:
            for c in colors:
                if not isinstance(c, str):
                    return {"ok": False, "error": f"colors must be hex strings, got {c!r}"}
                cs = c.strip()
                if not (cs.startswith("#") and len(cs) in (4, 7)):
                    return {"ok": False, "error": f"invalid color {c!r}: use '#rrggbb' or '#rgb'"}
                clean_colors.append(cs)
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"ok": False, "error": f"Layer is not a vector layer"}

        data = []
        field_idx = layer.fields().indexFromName(field_name)
        if field_idx == -1:
            return {"ok": False, "error": f"Field {field_name!r} not found"}

        # F9: pull only the one field, no geometry — cuts allocation for big layers.
        from qgis.core import QgsFeatureRequest
        req = QgsFeatureRequest().setFlags(_no_geometry_flag())
        req.setSubsetOfAttributes([field_name], layer.fields())
        event, owner = self._cancel.register()
        try:
            values = {}
            feature_iter = layer.getFeatures(req)
            scanned = 0
            for i, feature in enumerate(feature_iter):
                if owner and event is not None and event.is_set():
                    return {"ok": False, "error": "cancelled by user", "cancelled": True}
                if i >= DEFAULT_FEATURE_SCAN_LIMIT:
                    break
                attrs = feature.attributes()
                val = attrs[field_idx] if field_idx < len(attrs) else None
                if val not in values:
                    values[val] = 0
                values[val] += 1
                scanned = i + 1
                # Yield to the event loop every 100 features to prevent UI freeze
                if i % EVENT_PUMP_INTERVAL == 0:
                    QCoreApplication.processEvents()
        finally:
            if owner:
                self._cancel.release(event)

        # Sort by count
        sorted_items = sorted(values.items(), key=lambda x: x[1], reverse=True)[:20]  # top 20
        data = [{"label": str(k), "value": v} for k, v in sorted_items]

        result = {
            "ok": True,
            "chart_type": chart_type,
            "title": f"{field_name} in {layer.name()}",
            "data": data,
            "field": field_name,
            "layer_name": layer.name(),
            "scanned_features": scanned,
            "truncated": scanned >= DEFAULT_FEATURE_SCAN_LIMIT,
        }
        if clean_colors:
            result["colors"] = clean_colors
        return result

    def get_layer_statistics(self, layer_id, field_name=None):
        """Calculate statistics for a vector layer or specific field."""
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return {"ok": False, "error": f"No layer with id {layer_id!r}"}
        if not isinstance(layer, QgsVectorLayer):
            return {"ok": False, "error": f"Layer is not a vector layer"}

        stats = {
            "layer_name": layer.name(),
            "total_features": layer.featureCount(),
            "valid": layer.isValid(),
            "crs": layer.crs().authid() if layer.crs().isValid() else None,
            "geometry_type": layer.geometryType(),
        }

        if field_name:
            field_idx = layer.fields().indexFromName(field_name)
            if field_idx == -1:
                return {"ok": False, "error": f"Field {field_name!r} not found"}

            # F9: only the requested attribute, no geometry; stream values so
            # large layers don't get materialised into Python lists.
            from qgis.core import QgsFeatureRequest
            req = QgsFeatureRequest().setFlags(_no_geometry_flag())
            req.setSubsetOfAttributes([field_name], layer.fields())
            event, owner = self._cancel.register()
            try:
                scanned = 0
                distinct_values = set()
                null_count = 0
                numeric_count = 0
                numeric_min = None
                numeric_max = None
                numeric_sum = 0.0
                numeric_sum_sq = 0.0
                for i, feature in enumerate(layer.getFeatures(req)):
                    if owner and event is not None and event.is_set():
                        return {"ok": False, "error": "cancelled by user", "cancelled": True}
                    if i >= DEFAULT_FEATURE_SCAN_LIMIT:
                        break
                    attrs = feature.attributes()
                    val = attrs[field_idx] if field_idx < len(attrs) else None
                    scanned = i + 1
                    distinct_values.add(val)
                    if val is None:
                        null_count += 1
                    if val is not None:
                        try:
                            num = float(val)
                        except (TypeError, ValueError):
                            num = None
                        if num is not None:
                            numeric_count += 1
                            numeric_sum += num
                            numeric_sum_sq += num * num
                            numeric_min = num if numeric_min is None else min(numeric_min, num)
                            numeric_max = num if numeric_max is None else max(numeric_max, num)
                    # Yield to the event loop every 100 features to prevent UI freeze
                    if i % EVENT_PUMP_INTERVAL == 0:
                        QCoreApplication.processEvents()
            finally:
                if owner:
                    self._cancel.release(event)

            stats["field"] = field_name
            stats["distinct_count"] = len(distinct_values)
            stats["null_count"] = null_count
            stats["scanned_features"] = scanned
            stats["truncated"] = scanned >= DEFAULT_FEATURE_SCAN_LIMIT

            if numeric_count:
                stats["min"] = numeric_min
                stats["max"] = numeric_max
                stats["mean"] = numeric_sum / numeric_count
                stats["sum"] = numeric_sum
                stats["count"] = numeric_count
                if numeric_count > 1:
                    variance = (numeric_sum_sq - ((numeric_sum * numeric_sum) / numeric_count)) / (numeric_count - 1)
                    stats["stdev"] = max(variance, 0.0) ** 0.5
                else:
                    stats["stdev"] = 0.0

        return {"ok": True, "statistics": stats}
