"""Unit checks for reusable QGIS processing task helper construction."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core import processing_tasks


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeContext:
    pass


class _FakeFeedback:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _FakeTask:
    CanCancel = 1

    def __init__(self, algorithm, parameters, context, feedback, flags=CanCancel):
        self.algorithm = algorithm
        self.parameters = parameters
        self.context = context
        self.feedback = feedback
        self.flags = flags
        self.executed = _Signal()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        self.executed.emit(False, {})


class _FakeRegistry:
    def __init__(self):
        self.created_ids = []

    def createAlgorithmById(self, alg_id):
        self.created_ids.append(alg_id)
        return {"id": alg_id}


class _FakeTaskManager:
    def __init__(self):
        self.tasks = []
        self.auto_finish = True

    def addTask(self, task):
        self.tasks.append(task)
        if self.auto_finish:
            task.executed.emit(True, {"OUTPUT": "memory:"})


class _FakeApplication:
    registry = _FakeRegistry()
    manager = _FakeTaskManager()

    @classmethod
    def processingRegistry(cls):
        return cls.registry

    @classmethod
    def taskManager(cls):
        return cls.manager


def _reset_fakes():
    _FakeApplication.registry = _FakeRegistry()
    _FakeApplication.manager = _FakeTaskManager()


class _Executor:
    def __init__(self):
        self.calls = []
        self.timeouts = []

    def run_sync(self, fn, timeout=None):
        self.calls.append(fn)
        self.timeouts.append(timeout)
        return fn()


def main():
    processing_tasks.QgsApplication = _FakeApplication
    processing_tasks.QgsProcessingAlgRunnerTask = _FakeTask
    processing_tasks.QgsProcessingContext = _FakeContext
    processing_tasks.QgsProcessingFeedback = _FakeFeedback

    _reset_fakes()
    executor = _Executor()
    result = processing_tasks.run_processing_algorithm_task(
        executor,
        "native:centroids",
        {"INPUT": "layer", "OUTPUT": "memory:"},
    )

    assert result["ok"] is True, result
    assert result["results"] == {"OUTPUT": "memory:"}
    assert isinstance(result["elapsed"], float)
    assert len(executor.calls) == 1
    assert executor.timeouts == [None]
    assert _FakeApplication.registry.created_ids == ["native:centroids"]

    task = _FakeApplication.manager.tasks[0]
    assert task.algorithm == {"id": "native:centroids"}
    assert task.parameters == {"INPUT": "layer", "OUTPUT": "memory:"}
    assert isinstance(task.context, _FakeContext)
    assert isinstance(task.feedback, _FakeFeedback)
    assert task._agenticgis_context is task.context
    assert task._agenticgis_feedback is task.feedback
    assert task.flags == _FakeTask.CanCancel

    _reset_fakes()
    _FakeApplication.manager.auto_finish = False
    cancel_result = processing_tasks.run_processing_algorithm_task(
        _Executor(),
        "native:centroids",
        {},
        cancel=lambda: True,
        poll_interval=0.001,
    )

    cancelled_task = _FakeApplication.manager.tasks[0]
    assert cancel_result["ok"] is False, cancel_result
    assert cancel_result["cancelled"] is True, cancel_result
    assert cancel_result["errors"] == ["cancelled by user"], cancel_result
    assert cancelled_task.cancelled is True
    assert cancelled_task.feedback.cancelled is True

    _reset_fakes()
    timeout_executor = _Executor()
    result = processing_tasks.run_processing_algorithm_task(
        timeout_executor,
        "native:extractbyattribute",
        {"INPUT": "layer", "OUTPUT": "memory:"},
        main_thread_timeout=300.0,
    )
    assert result["ok"] is True, result
    assert timeout_executor.timeouts == [300.0]


if __name__ == "__main__":
    main()
