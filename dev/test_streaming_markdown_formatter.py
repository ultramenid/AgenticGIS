"""Regression check that streaming agent text uses the full markdown formatter."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.agent_turn_bubble import AgentTurnBubble


def main():
    app = QApplication.instance() or QApplication([])
    turn = AgentTurnBubble()

    turn.set_streaming_text("| Field | Value |\n| --- | --- |\n| Name | Roads |")

    html = turn.text_lbl.text()
    assert "<pre" in html, "streaming text should render markdown tables before finalization"
    assert "white-space:pre" in html
    assert "Field" in html
    assert "Roads" in html

    turn.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
