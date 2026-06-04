"""Regression check for readable responsive ask-user overlay sizing."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.chat_dock import ChatDock


def main():
    app = QApplication.instance() or QApplication([])
    dock = ChatDock(lambda: None, lambda: None, lambda: None)
    dock.resize(420, 640)
    dock.show()
    app.processEvents()

    dock._show_ask_user(
        "Allow AgenticGIS to access a path outside the loaded QGIS layers for this operation?",
        [
            {"label": "Allow once", "description": "Permit this operation, then ask again next time."},
            {"label": "Always allow", "description": "Permit external file, URL, and database access now and remember this choice."},
            {"label": "Deny", "description": "Block the operation and keep analysis inside loaded layers."},
        ],
        False,
    )
    app.processEvents()

    frame = dock._ask_card_frame
    assert frame.width() == min(560, max(280, dock.rect().width() - 32))
    assert frame.width() > 360
    assert frame.height() >= dock._ask_user_card.sizeHint().height()

    dock.resize(330, 640)
    app.processEvents()
    assert frame.width() <= dock.rect().width() - 32
    assert frame.width() >= 280

    dock.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
