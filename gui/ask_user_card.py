"""Popover card that lets the user answer a clarifying question from the agent.

The dock inserts one of these above the input bar when an ``ASK_USER`` event
arrives. Clicking an option button (or submitting the free-text field) emits
the ``submitted`` signal with ``{"choice": str|None, "free_text": str|None}``.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

_SURFACE = "#161616"
_INPUT_BG = "#1e1e1e"
_BORDER = "#2e2e2e"
_BORDER_SOFT = "#242424"
_TEXT = "#ececec"
_TEXT_2 = "#a0a0a0"
_TEXT_3 = "#707070"
_ACCENT = "#e0e0e0"
_ACCENT_HOV = "#c8c8c8"


class AskUserCard(QFrame):
    submitted = pyqtSignal(object)

    def __init__(self, question, options, allow_free_text=True, parent=None):
        super().__init__(parent)
        self.setObjectName("AskUserCard")
        self.setStyleSheet(f"""
            QFrame#AskUserCard {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)
        self._options = list(options)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        header = QLabel("Agent needs input")
        header.setStyleSheet(
            f"color:{_TEXT_3}; font-size:10px; letter-spacing:0.06em; "
            f"text-transform:uppercase; background:transparent; border:none;"
        )
        outer.addWidget(header)

        q = QLabel(question)
        q.setWordWrap(True)
        q.setStyleSheet(
            f"color:{_TEXT}; font-size:13px; font-weight:500; "
            f"background:transparent; border:none;"
        )
        outer.addWidget(q)

        opt_row = QHBoxLayout()
        opt_row.setContentsMargins(0, 0, 0, 0)
        opt_row.setSpacing(6)
        for opt in self._options:
            btn = QPushButton(opt.get("label", "?"))
            tip = opt.get("description", "")
            if tip:
                btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_SURFACE};
                    color: {_TEXT};
                    border: 1px solid {_BORDER};
                    border-radius: 8px;
                    padding: 6px 12px;
                    font-size: 12px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {_BORDER_SOFT};
                    border-color: {_TEXT_3};
                }}
                QPushButton:pressed {{
                    background-color: {_ACCENT};
                    color: {_SURFACE};
                }}
            """)
            label = opt.get("label", "")
            btn.clicked.connect(lambda _checked=False, lbl=label: self._on_option(lbl))
            opt_row.addWidget(btn)
        opt_row.addStretch(1)
        outer.addLayout(opt_row)

        if allow_free_text:
            ft_row = QHBoxLayout()
            ft_row.setContentsMargins(0, 0, 0, 0)
            ft_row.setSpacing(6)
            self._free_text = QLineEdit()
            self._free_text.setPlaceholderText("Or type your own answer\u2026")
            self._free_text.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {_SURFACE};
                    color: {_TEXT};
                    border: 1px solid {_BORDER};
                    border-radius: 8px;
                    padding: 6px 10px;
                    font-size: 12px;
                    selection-background-color: {_TEXT};
                    selection-color: {_SURFACE};
                }}
            """)
            self._free_text.returnPressed.connect(self._on_free_text)
            ft_row.addWidget(self._free_text, 1)

            send = QPushButton("Send")
            send.setCursor(Qt.PointingHandCursor)
            send.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_ACCENT};
                    color: {_SURFACE};
                    border: none;
                    border-radius: 8px;
                    padding: 6px 14px;
                    font-size: 12px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background-color: {_ACCENT_HOV}; }}
                QPushButton:disabled {{ background-color: {_BORDER}; color: {_TEXT_3}; }}
            """)
            send.clicked.connect(self._on_free_text)
            ft_row.addWidget(send)
            outer.addLayout(ft_row)
        else:
            self._free_text = None

    def _on_option(self, label):
        self.submitted.emit({"choice": label, "free_text": None})

    def _on_free_text(self):
        if self._free_text is None:
            return
        text = self._free_text.text().strip()
        if not text:
            return
        self.submitted.emit({"choice": None, "free_text": text})
