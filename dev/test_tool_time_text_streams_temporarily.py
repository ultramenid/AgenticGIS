"""Regression check that tool-time text is visible but not final-answer text."""

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
        "name": "run_processing",
        "input": {"alg_id": "native:statisticsbycategories"},
    }))

    turn = dock._current_agent_turn
    assert turn is not None
    assert "Processing" in turn.text_lbl.text()
    assert "run_processing" in turn.text_lbl.text()

    dock._on_event(AgentEvent(EventType.TEXT, {
        "text": "Computing category statistics while the tool runs. "
    }))

    assert "Computing category statistics" in turn.text_lbl.text()

    dock._on_event(AgentEvent(EventType.TOOL_RESULT, {
        "name": "run_processing",
        "result": '{"ok": true}',
        "is_error": False,
    }))
    assert dock._typing_widget is None
    assert "Finished" in turn.text_lbl.text()
    assert "Preparing answer" in turn.text_lbl.text()

    dock._on_event(AgentEvent(EventType.TEXT, {
        "text": "The category statistics are ready."
    }))

    final_html = turn.text_lbl.text()
    assert "The category statistics are ready." in final_html
    assert "Computing category statistics" not in final_html

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
