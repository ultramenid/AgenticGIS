"""Regression check for compact ask-user prompt layout."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication, QLabel, QPushButton

from AgenticGis.gui.ask_user_card import AskUserCard, _OptionRow


def main():
    app = QApplication.instance() or QApplication([])
    card = AskUserCard(
        "Allow external access for this one operation?\n"
        "run PyQGIS code that may access files, URLs, or sources outside loaded layers",
        [
            {"label": "Allow once", "description": "Permit this access."},
            {"label": "Always allow", "description": "Permit this access and remember it."},
            {"label": "Deny", "description": "Block this access."},
        ],
        allow_free_text=False,
    )
    card.resize(260, card.sizeHint().height())
    card.show()
    app.processEvents()

    labels = "\n".join(label.text() for label in card.findChildren(QLabel))
    assert "run PyQGIS code" in labels
    assert "Permit this access." in labels
    assert "Permit this access and remember it." in labels
    assert "Block this access." in labels

    option_buttons = [
        child for child in card.findChildren(QLabel)
        if child.objectName() == "AskUserOptionTitle"
    ]
    assert len(option_buttons) == 3
    assert card.sizeHint().width() >= 360
    assert card.sizeHint().width() <= 560

    send_buttons = card.findChildren(QPushButton)
    assert len(send_buttons) == 0

    long_row = _OptionRow(
        "Always allow",
        "Permit external file, URL, and database access now and remember this choice for later requests.",
    )
    long_row.resize(260, long_row.sizeHint().height())
    long_row.show()
    app.processEvents()
    assert long_row.sizeHint().height() >= 56

    long_row.deleteLater()
    card.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
