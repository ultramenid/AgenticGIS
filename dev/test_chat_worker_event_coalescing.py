"""Regression check that ChatWorker coalesces streaming event floods."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.backends.base import AgentEvent, EventType
from AgenticGis.gui.chat_dock import ChatWorker


class FloodBackend:
    def send(self, message, history, emit, should_stop):
        for _ in range(250):
            emit(AgentEvent(EventType.TEXT, {"text": "x"}))
        emit(AgentEvent(EventType.TOOL_USE, {"name": "list_layers", "input": {}}))
        for _ in range(250):
            emit(AgentEvent(EventType.TEXT, {"text": "y"}))
        return history


def main():
    app = QApplication.instance() or QApplication([])
    worker = ChatWorker(FloodBackend(), "hi", [])
    events = []
    histories = []
    worker.event.connect(events.append)
    worker.finished_history.connect(histories.append)

    worker.run()

    text_events = [ev for ev in events if ev.type == EventType.TEXT]
    assert len(text_events) == 2, f"expected 2 coalesced text events, got {len(text_events)}"
    assert text_events[0].data["text"] == "x" * 250
    assert events[1].type == EventType.TOOL_USE
    assert text_events[1].data["text"] == "y" * 250
    assert histories == [[]]

    app.processEvents()


if __name__ == "__main__":
    main()
