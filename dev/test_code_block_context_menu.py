"""Regression checks for copyable markdown code blocks."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication, QWidget

from AgenticGis.gui.agent_turn_bubble import AgentTurnBubble
from AgenticGis.gui.message_bubble import (
    _build_code_context_menu,
    _extract_fenced_code_blocks,
)


def main():
    app = QApplication.instance() or QApplication([])

    text = (
        "Run this:\n"
        "```python\n"
        "print('alpha')\n"
        "```\n"
        "Then this:\n"
        "```sql\n"
        "select 1;\n"
        "```"
    )
    blocks = _extract_fenced_code_blocks(text)
    assert blocks == ["print('alpha')\n", "select 1;\n"]

    parent = QWidget()
    menu = _build_code_context_menu(parent, blocks, copy_message=text)
    labels = [action.text() for action in menu.actions()]
    assert "Code block" in labels
    assert "Copy code block 1" in labels
    assert "Copy code block 2" in labels
    assert "Copy message" not in labels
    assert "background-color: #202020" in menu.styleSheet()

    copy_first = next(action for action in menu.actions() if action.text() == "Copy code block 1")
    copy_first.trigger()
    assert QApplication.clipboard().text() == "print('alpha')\n"

    plain_menu = _build_code_context_menu(parent, [], copy_message="plain response")
    plain_labels = [action.text() for action in plain_menu.actions()]
    assert "Message" in plain_labels
    assert "Copy message" in plain_labels

    turn = AgentTurnBubble()
    turn.finalize_text(text)
    assert turn.text_lbl.contextMenuPolicy() != 0
    assert _extract_fenced_code_blocks(turn._stream_text) == blocks

    turn.deleteLater()
    parent.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
