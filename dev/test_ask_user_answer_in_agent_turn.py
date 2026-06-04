"""Regression check that ask-user answers do not create noisy user bubbles."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication, QLabel

from AgenticGis.backends.base import AgentEvent, EventType
from AgenticGis.gui.chat_dock import ChatDock


def _visible_widgets(layout):
    widgets = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        widget = item.widget()
        if widget is not None:
            widgets.append(widget)
    return widgets


def main():
    app = QApplication.instance() or QApplication([])
    dock = ChatDock(lambda: None, lambda: None, lambda: None)
    dock.resize(420, 640)
    dock.show()
    app.processEvents()
    dock._on_event(
        AgentEvent(EventType.THINKING, {"text": "Need permission before continuing."})
    )
    app.processEvents()

    dock._show_ask_user(
        "Allow AgenticGIS to access a path outside the loaded QGIS layers?",
        [
            {"label": "Allow once", "description": "Permit this operation."},
            {"label": "Deny", "description": "Block this operation."},
        ],
        False,
    )
    app.processEvents()
    before = len(_visible_widgets(dock.transcript_layout))

    dock._resolve_ask_user({"choice": "Allow once", "free_text": None, "cancelled": False})
    app.processEvents()
    after = len(_visible_widgets(dock.transcript_layout))

    assert after == before, "popup answer should not add a separate transcript bubble"
    turn = dock._current_agent_turn
    assert turn is not None
    labels = "\n".join(label.text() for label in turn.findChildren(QLabel))
    assert "User chose: Allow once" in labels
    all_labels = "\n".join(
        label.text()
        for widget in _visible_widgets(dock.transcript_layout)
        for label in widget.findChildren(QLabel)
    )
    assert "→ Allow once" not in all_labels

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
