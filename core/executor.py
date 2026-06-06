"""Run callables on the QGIS main thread from any background thread.

PyQGIS objects (``QgsProject``, layers, the map canvas, ``iface`` and most of
the Processing framework) are **not thread-safe** and must be touched only on
the GUI/main thread. Agent backends, however, run their loops on worker
threads so the UI stays responsive. ``MainThreadExecutor`` is the bridge: a
worker thread submits a callable, it is executed on the main thread via a
queued Qt signal, and the worker blocks until the result (or exception) is
available.

The executor object MUST be constructed on the main thread.

Cancellation & lifecycle safety
------------------------------
Every submitted job gets a unique id. The executor tracks a single
"current job id" so a slow main-thread operation that survives a
``TimeoutError`` cannot silently write into a successor's result slot.
The slot is also guarded by an ``in_flight`` flag to prevent the queued
signal from queuing additional work while the previous one is still
running (the worker still waits on its own ``threading.Event``).
"""

import threading
import time
import traceback

from qgis.PyQt.QtCore import QObject, QThread, pyqtSignal

from .dev_logging import log_event
from .qt_compat import QUEUED_CONNECTION


class _Job:
    __slots__ = ("id", "fn", "event", "result", "error", "cancelled")

    def __init__(self, jid, fn):
        self.id = jid
        self.fn = fn
        self.event = threading.Event()
        self.result = None
        self.error = None
        self.cancelled = False


class MainThreadExecutor(QObject):
    # Emitted from a worker thread; the queued connection guarantees the slot
    # runs on the thread this QObject lives on (the main thread).
    _submitted = pyqtSignal(object)

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.config = config
        # Serialise access to the job id and in-flight state.
        self._lock = threading.Lock()
        self._job_seq = 0
        self._current_job_id = None
        self._submitted.connect(self._execute, QUEUED_CONNECTION)

    def _next_id(self):
        with self._lock:
            self._job_seq += 1
            return self._job_seq

    def run_sync(self, fn, timeout=None):
        """Execute ``fn`` on the main thread and return its result.

        Raises whatever ``fn`` raises, or ``TimeoutError`` if the main thread
        does not service the job within ``timeout`` seconds (``None`` waits
        forever).
        """
        if timeout is None:
            timeout = 60.0
            if self.config is not None:
                timeout = self.config.get("main_thread_timeout", timeout)
        # Fast path: already on the main thread — just call it.
        if QThread.currentThread() is self.thread():
            return fn()

        jid = self._next_id()
        job = _Job(jid, fn)
        start = time.perf_counter()
        with self._lock:
            # If a prior job is still in-flight, refuse rather than queue — the
            # caller is either retrying (let them time out cleanly) or
            # contending (let the OS scheduler sort it). This prevents an
            # unbounded queue of zombie jobs on the main thread.
            if self._current_job_id is not None:
                raise RuntimeError(
                    "AgenticGIS: main thread is busy with another operation; "
                    "do not stack MainThreadExecutor.run_sync calls."
                )
            self._current_job_id = jid
        try:
            log_event("main_thread.queue", job_id=jid, timeout=timeout)
            self._submitted.emit(job)
            # Process the event loop periodically while waiting, so we don't
            # completely block if the main thread is temporarily busy.
            wait_interval = 0.05  # 50ms chunks
            elapsed = 0.0
            while not job.event.wait(wait_interval):
                # Check if we should stop and if the worker thread is still alive
                if elapsed >= timeout:
                    # Mark the job cancelled so a late write from the main thread
                    # becomes a no-op (see _execute).
                    with self._lock:
                        if self._current_job_id == jid:
                            self._current_job_id = None
                    job.cancelled = True
                    log_event(
                        "main_thread.timeout",
                        job_id=jid,
                        elapsed_ms=int((time.perf_counter() - start) * 1000),
                        timeout=timeout,
                    )
                    raise TimeoutError(
                        "AgenticGIS: main-thread operation timed out "
                        f"after {timeout}s (the QGIS UI may be busy)."
                    )
                elapsed += wait_interval
            if job.error is not None:
                raise job.error
            # If the job was cancelled during the wait, raise TimeoutError
            # so callers can handle it uniformly.
            if job.cancelled:
                log_event(
                    "main_thread.cancelled",
                    job_id=jid,
                    elapsed_ms=int((time.perf_counter() - start) * 1000),
                )
                raise TimeoutError(
                    "AgenticGIS: main-thread operation was cancelled."
                )
            log_event(
                "main_thread.result",
                job_id=jid,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
            return job.result
        finally:
            with self._lock:
                if self._current_job_id == jid:
                    self._current_job_id = None

    def _execute(self, job):
        # Guard against a late-firing slot for a job that already timed out
        # (the worker's TimeoutError has been raised, the worker has moved on,
        # the main thread is just slow). Skip the work entirely.
        if job.cancelled:
            return
        start = time.perf_counter()
        log_event("main_thread.execute.start", job_id=job.id)
        try:
            job.result = job.fn()
        except BaseException as exc:  # noqa: BLE001 — propagate to caller
            job.error = exc
            log_event(
                "main_thread.execute.error",
                job_id=job.id,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
                error_type=type(exc).__name__,
                error=str(exc),
                traceback="".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__, limit=4)
                ),
            )
        else:
            log_event(
                "main_thread.execute.end",
                job_id=job.id,
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )
        finally:
            job.event.set()
