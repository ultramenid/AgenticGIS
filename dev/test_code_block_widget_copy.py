"""Regression checks for standalone CodeBlockWidget copy behavior."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.code_block import CodeBlockWidget, _MENU_STYLE


def main():
    app = QApplication.instance() or QApplication([])
    widget = CodeBlockWidget("print('copy me')\n", "python")

    assert widget.editor.contextMenuPolicy() == Qt.CustomContextMenu
    assert "background-color: #202020" in _MENU_STYLE

    widget._copy_code()
    assert QApplication.clipboard().text() == "print('copy me')\n"

    widget.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
