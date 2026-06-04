"""Regression checks for stopping an in-flight LLM turn."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.backends.base import AgentEvent, EventType
from AgenticGis.gui.chat_dock import ChatDock


class _FakeWorker:
    def __init__(self):
        self.stopped = False
        self.event = _FakeSignal()
        self.finished_history = _FakeSignal()
        self.finished = _FakeSignal()

    def stop(self):
        self.stopped = True


class _FakeSignal:
    def __init__(self):
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True


class _ImmediateBackend:
    def validate(self):
        return None

    def send(self, message, history, emit, should_stop):
        return list(history) + [{"role": "user", "content": message}]


def main():
    app = QApplication.instance() or QApplication([])
    cancel_calls = []
    dock = ChatDock(lambda: None, lambda: None, lambda: cancel_calls.append(True))

    dock._set_tool_progress("Thinking...")
    worker = _FakeWorker()
    dock._worker = worker

    dock._on_stop()

    assert worker.stopped is True
    assert cancel_calls == [True]
    assert dock._stop_requested is True
    before = dock._current_agent_turn.text_lbl.text()

    dock._on_event(AgentEvent(EventType.TEXT, {"text": "late token"}))
    dock._on_event(AgentEvent(EventType.THINKING, {"text": "late reasoning"}))

    assert dock._current_agent_turn.text_lbl.text() == before
    assert "late token" not in dock._current_agent_turn.text_lbl.text()
    assert "late reasoning" not in dock._current_agent_turn.text_lbl.text()

    dock._on_finished(None, worker)
    dock._on_worker_thread_finished(worker)

    assert dock._stop_requested is False
    assert dock.send_btn.isEnabled() is True
    assert worker.finished.disconnected is True

    backend = _ImmediateBackend()
    dock._get_backend = lambda: backend
    dock.input.setPlainText("hi")
    dock._on_send()
    assert dock._worker is not None
    assert dock._worker.wait(3000) is True
    app.processEvents()
    assert dock._worker is None
    assert dock.send_btn.isEnabled() is True

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
