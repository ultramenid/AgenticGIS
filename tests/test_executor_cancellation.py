"""Tests for the cancellation token semantics added to MainThreadExecutor."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- mock QGIS dependencies BEFORE importing the module --------------------
class _MockQSettings:
    def __init__(self):
        self._store = {}
    def value(self, key, default=None):
        return self._store.get(key, default)
    def setValue(self, key, value):
        self._store[key] = value

class _MockThreadSelf:
    """Stand-in for QThread.currentThread() — returns 'self' so the
    executor's fast path triggers."""
    def __init__(self):
        self.is_self = True
    def __eq__(self, other):
        return isinstance(other, _MockThreadSelf) and other.is_self is self.is_self
    def __hash__(self):
        return id(self)

class _MockThreadOther:
    """A different thread — used to drive the cross-thread path."""
    def __eq__(self, other):
        return False
    def __hash__(self):
        return id(self)

class _MockQtCore:
    QueuedConnection = 1
    QSettings = _MockQSettings
    class QObject:
        _self_thread = _MockThreadSelf()
        def __init__(self, *a, **k): pass
        def thread(self):
            return getattr(self.__class__, "_self_thread", _MockThreadSelf())
    class QThread:
        @staticmethod
        def currentThread():
            return _MockThreadSelf()
    class Qt:
        QueuedConnection = 1
    @staticmethod
    def pyqtSignal(*a, **k):
        class _Sig:
            def __init__(self):
                self._slot = None
            def connect(self, slot, *a, **k):
                self._slot = slot
            def emit(self, *args, **k):
                if self._slot is not None:
                    self._slot(*args)
        return _Sig()

class _PyQt:
    QtCore = _MockQtCore

class _Qgis:
    PyQt = _PyQt

# Register the mocks. Use SimpleNamespace so isinstance(module, ModuleType)
# works for the LSP / runtime checks.
from types import SimpleNamespace
sys.modules["qgis"] = SimpleNamespace(PyQt=_PyQt)
sys.modules["qgis.PyQt"] = SimpleNamespace(QtCore=_MockQtCore)
sys.modules["qgis.PyQt.QtCore"] = _MockQtCore

import importlib

executor_mod = importlib.import_module("core.executor")
MainThreadExecutor = executor_mod.MainThreadExecutor


def test_executor_runs_synchronously_on_main_thread():
    """When the caller is on the main thread, run_sync should not enqueue."""
    ex = MainThreadExecutor()
    result = []
    out = ex.run_sync(lambda: (result.append(1), 42)[1])
    assert out == 42
    assert result == [1]


def test_executor_returns_function_result():
    """A trivial return value should pass through unchanged."""
    ex = MainThreadExecutor()
    assert ex.run_sync(lambda: "ok") == "ok"


def test_executor_propagates_exceptions():
    """Exceptions raised by fn should propagate to the caller."""
    ex = MainThreadExecutor()
    raised = False
    try:
        ex.run_sync(lambda: (_ for _ in ()).throw(ValueError("boom")))
    except ValueError as exc:
        raised = True
        assert "boom" in str(exc)
    assert raised


def test_executor_job_ids_are_unique_and_monotonic():
    """Each call to _next_id returns a fresh, increasing id."""
    ex = MainThreadExecutor()
    a = ex._next_id()
    b = ex._next_id()
    c = ex._next_id()
    assert a < b < c


def test_executor_rejects_when_main_thread_busy(monkeypatch):
    """A second run_sync call while a previous one is in-flight on the
    main thread must raise rather than queue a zombie job."""
    ex = MainThreadExecutor()
    ex._current_job_id = 999  # simulate an in-flight job
    # Force the cross-thread branch by making QThread.currentThread
    # return *something different* from the executor's own thread().
    class _Other:
        pass
    other_a = _Other()
    other_b = _Other()
    # The executor imported QThread into its own namespace — patch there.
    monkeypatch.setattr(executor_mod.QThread, "currentThread", staticmethod(lambda: other_a))
    ex.thread = lambda: other_b
    raised = False
    try:
        ex.run_sync(lambda: 1, timeout=0.2)
    except RuntimeError as exc:
        raised = True
        assert "main thread is busy" in str(exc).lower()
    finally:
        ex._current_job_id = None
    assert raised, "Expected RuntimeError when main thread is busy"


def test_executor_clears_in_flight_after_call():
    """After a successful fast-path call, _current_job_id is None so the
    next call can proceed."""
    ex = MainThreadExecutor()
    ex.run_sync(lambda: None)
    assert ex._current_job_id is None


def test_executor_ignores_cancelled_job_in_slot():
    """When a job was cancelled (timed out), the late _execute call must
    not write to result/error — the slot is a no-op."""
    ex = MainThreadExecutor()
    # Build a job, mark it cancelled, and call _execute directly. The
    # event should not be set (so the worker that already timed out is
    # never woken by stale data) and the cancelled flag stays True.
    jid = ex._next_id()
    job = executor_mod._Job(jid, lambda: "should not run")
    job.cancelled = True
    ex._execute(job)
    assert job.result is None
    assert job.error is None
    assert job.cancelled is True
    # Event is still unset (we never set it), so a worker that has
    # already moved on is unaffected.
    assert not job.event.is_set()


def test_executor_slot_sets_event_on_success():
    """A normal slot call sets the event so the worker wakes up."""
    ex = MainThreadExecutor()
    jid = ex._next_id()
    job = executor_mod._Job(jid, lambda: 99)
    ex._execute(job)
    assert job.result == 99
    assert job.error is None
    assert job.event.is_set()


def test_executor_slot_captures_exception():
    """If the job function raises, the slot stores the exception and sets
    the event so the worker's ``raise job.error`` re-raises it."""
    ex = MainThreadExecutor()
    jid = ex._next_id()
    def _bad():
        raise ValueError("nope")
    job = executor_mod._Job(jid, _bad)
    ex._execute(job)
    assert isinstance(job.error, ValueError)
    assert "nope" in str(job.error)
    assert job.event.is_set()
