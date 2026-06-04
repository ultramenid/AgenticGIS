"""Regression check that completed tool turns do not show a summary line."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication, QLabel

from AgenticGis.gui.agent_turn_bubble import AgentTurnBubble


def main():
    app = QApplication.instance() or QApplication([])
    turn = AgentTurnBubble()

    row = turn.add_tool("run_pyqgis", {"code": "result = 1"})
    row.set_result('{"ok": true, "result": 1}', False)
    turn.finalize_text("Done")

    label_text = "\n".join(label.text() for label in turn.findChildren(QLabel))
    assert "tools" not in label_text, "tool summary text should not be rendered"
    assert "total" not in label_text, "tool elapsed summary should not be rendered"

    turn.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
