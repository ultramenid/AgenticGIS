"""Run callables on the QGIS main thread from any background thread.

PyQGIS objects (``QgsProject``, layers, the map canvas, ``iface`` and most of
the Processing framework) are **not thread-safe** and must be touched only on
the GUI/main thread. Agent backends, however, run their loops on worker
threads so the UI stays responsive. ``MainThreadExecutor`` is the bridge: a
worker thread submits a callable, it is executed on the main thread via a
queued Qt signal, and the worker blocks until the result (or exception) is
available.

The executor object MUST be constructed on the main thread.
"""

import threading

from qgis.PyQt.QtCore import QObject, QThread, Qt, pyqtSignal


class _Job:
    __slots__ = ("fn", "event", "result", "error")

    def __init__(self, fn):
        self.fn = fn
        self.event = threading.Event()
        self.result = None
        self.error = None


class MainThreadExecutor(QObject):
    # Emitted from a worker thread; the queued connection guarantees the slot
    # runs on the thread this QObject lives on (the main thread).
    _submitted = pyqtSignal(object)

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.config = config
        self._submitted.connect(self._execute, Qt.QueuedConnection)

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

        job = _Job(fn)
        self._submitted.emit(job)
        if not job.event.wait(timeout):
            raise TimeoutError(
                "AgenticGIS: main-thread operation timed out "
                f"after {timeout}s (the QGIS UI may be busy)."
            )
        if job.error is not None:
            raise job.error
        return job.result

    def _execute(self, job):
        try:
            job.result = job.fn()
        except BaseException as exc:  # noqa: BLE001 — propagate to caller
            job.error = exc
        finally:
            job.event.set()
