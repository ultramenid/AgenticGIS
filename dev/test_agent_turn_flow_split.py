"""Regression check for clean thinking/tool/final-answer flow separation."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication, QLabel

from AgenticGis.backends.base import AgentEvent, EventType
from AgenticGis.gui.chat_dock import ChatDock


def main():
    app = QApplication.instance() or QApplication([])
    dock = ChatDock(lambda: None, lambda: None, lambda: None)

    dock._on_event(AgentEvent(EventType.THINKING, {"text": "Checking layer schema"}))
    dock._on_event(AgentEvent(EventType.TEXT, {"text": "I will inspect the layer first. "}))
    dock._on_event(AgentEvent(EventType.TOOL_USE, {
        "name": "analyze_layer",
        "input": {"layer_id": "roads"},
    }))
    dock._on_event(AgentEvent(EventType.TEXT, {"text": "Scanning attributes while the tool runs. "}))
    dock._on_event(AgentEvent(EventType.TOOL_RESULT, {
        "name": "analyze_layer",
        "result": '{"ok": true, "scanned_features": 10}',
        "is_error": False,
    }))
    dock._on_event(AgentEvent(EventType.TEXT, {
        "text": "| Field | Value |\n| --- | --- |\n| Name | Roads |\nFinal answer."
    }))

    turn = dock._current_agent_turn
    assert turn is not None
    main_html = turn.text_lbl.text()

    assert "<pre" in main_html, "final answer should stream through markdown formatter"
    assert "white-space:pre" in main_html
    assert "Final answer." in main_html
    assert "Scanning attributes while the tool runs" not in main_html, (
        "tool-time reasoning should not be duplicated in the final answer"
    )
    assert "I will inspect the layer first" not in main_html, (
        "pre-tool prose should be folded into thinking, not final answer"
    )

    labels = "\n".join(label.text() for label in turn.findChildren(QLabel))
    assert "I will inspect the layer first" in labels, (
        "pre-tool prose should remain visible in the thinking block"
    )
    assert "Scanning attributes while the tool runs" in labels, (
        "tool-time reasoning should remain visible on the tool row"
    )

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
