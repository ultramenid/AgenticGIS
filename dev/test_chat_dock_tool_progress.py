"""Regression check for chat dock progress during post-tool LLM calls."""

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

    dock._on_event(AgentEvent(EventType.TOOL_USE, {
        "name": "run_pyqgis",
        "input": {"code": "result = 1"},
    }))
    dock._on_event(AgentEvent(EventType.TOOL_RESULT, {
        "name": "run_pyqgis",
        "result": '{"ok": true, "result": 1}',
        "is_error": False,
    }))

    assert dock._typing_widget is None, "post-tool progress should stay inside the agent turn"
    assert "Preparing answer" in dock._current_agent_turn.text_lbl.text()

    dock._on_event(AgentEvent(EventType.TEXT, {"text": "Done"}))
    assert dock._typing_widget is None, "typing indicator should hide when text streaming resumes"
    assert "Done" in dock._current_agent_turn.text_lbl.text()
    assert "Preparing answer" not in dock._current_agent_turn.text_lbl.text()

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
