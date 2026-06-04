"""Regression checks for chat input keyboard shortcuts."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent, QTextCursor
from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.chat_dock import ChatDock


def _key(key, modifiers=Qt.NoModifier):
    return QKeyEvent(QEvent.KeyPress, key, modifiers)


def main():
    app = QApplication.instance() or QApplication([])
    dock = ChatDock(lambda: None, lambda: None, lambda: None)

    sent = []
    stopped = []
    dock._on_send = lambda: sent.append(True)
    dock._on_stop = lambda: stopped.append(True)

    assert dock.input.viewportMargins().top() > 0

    dock.input.setPlainText("first")
    dock.input.moveCursor(QTextCursor.End)
    assert dock.eventFilter(dock.input, _key(Qt.Key_Return, Qt.AltModifier)) is True
    assert dock.input.toPlainText() == "first\n"
    assert sent == []

    dock.input.setPlainText("cmd")
    dock.input.moveCursor(QTextCursor.End)
    assert dock.eventFilter(dock.input, _key(Qt.Key_Return, Qt.MetaModifier)) is True
    assert dock.input.toPlainText() == "cmd\n"
    assert sent == []

    assert dock.eventFilter(dock.input, _key(Qt.Key_Return)) is True
    assert sent == [True]

    dock._worker = object()
    assert dock.eventFilter(dock.input, _key(Qt.Key_Escape)) is True
    assert stopped == []
    assert dock.eventFilter(dock.input, _key(Qt.Key_Escape)) is True
    assert stopped == [True]

    dock._remember_prompt("first prompt")
    dock._remember_prompt("second prompt")
    dock.input.setPlainText("draft")
    dock.input.moveCursor(QTextCursor.End)

    assert dock.eventFilter(dock.input, _key(Qt.Key_Up)) is True
    assert dock.input.toPlainText() == "second prompt"
    assert dock.eventFilter(dock.input, _key(Qt.Key_Up)) is True
    assert dock.input.toPlainText() == "first prompt"
    assert dock.eventFilter(dock.input, _key(Qt.Key_Down)) is True
    assert dock.input.toPlainText() == "second prompt"
    assert dock.eventFilter(dock.input, _key(Qt.Key_Down)) is True
    assert dock.input.toPlainText() == "draft"

    dock.input.setPlainText("line one\nline two")
    dock.input.moveCursor(QTextCursor.End)
    assert dock.eventFilter(dock.input, _key(Qt.Key_Up)) is False

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
