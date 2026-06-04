"""Regression check for clean thinking preview rendering."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.agent_turn_bubble import ThinkingBlock


def main():
    app = QApplication.instance() or QApplication([])
    block = ThinkingBlock()

    block.set_thinking_text("one\ntwo\nthree\nfour")
    block.toggle_collapse()

    html = block._text_lbl.text()
    assert "one<br>two<br>three" in html
    assert html.count("expand") == 1
    assert "&lt;span" not in html, "preview should not show escaped span markup"

    block.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
