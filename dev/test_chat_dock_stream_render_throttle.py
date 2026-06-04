"""Regression check that token floods do not repaint the response every event."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.backends.base import AgentEvent, EventType
from AgenticGis.gui.chat_dock import ChatDock


def main():
    app = QApplication.instance() or QApplication([])
    dock = ChatDock(lambda: None, lambda: None, lambda: None)
    turn = dock._get_or_create_agent_turn()

    calls = []
    original = turn.set_streaming_text

    def spy(text):
        calls.append(text)
        original(text)

    turn.set_streaming_text = spy

    for _ in range(100):
        dock._on_event(AgentEvent(EventType.TEXT, {"text": "a"}))

    assert len(calls) < 20, f"streaming rendered too often: {len(calls)} paints"
    dock._flush_stream_render()
    assert dock._current_text == "a" * 100
    assert calls[-1] == "a" * 100

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
