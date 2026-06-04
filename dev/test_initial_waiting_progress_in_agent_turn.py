"""Regression check that initial waiting state appears in the agent turn."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.chat_dock import ChatDock


def main():
    app = QApplication.instance() or QApplication([])
    dock = ChatDock(lambda: None, lambda: None, lambda: None)

    dock._set_status("Thinking", "#6f6f6f", spinning=True)
    dock._set_tool_progress("Thinking...")

    assert dock._typing_widget is None
    assert dock._current_agent_turn is not None
    assert "Thinking" in dock._current_agent_turn.text_lbl.text()
    assert dock._status_timer.isActive()
    status_first = dock.status.text()
    dock._tick_status()
    status_second = dock.status.text()
    assert status_first != status_second

    dock._current_agent_turn.clear_streaming_text()
    dock._current_agent_turn.set_streaming_text("Hi. How can I help?")
    assert "Thinking" not in dock._current_agent_turn.text_lbl.text()
    assert "Hi. How can I help?" in dock._current_agent_turn.text_lbl.text()

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
