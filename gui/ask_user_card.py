"""Popover card that lets the user answer a clarifying question from the agent.

The dock inserts one of these above the input bar when an ``ASK_USER`` event
arrives. Clicking an option button (or submitting the free-text field) emits
the ``submitted`` signal with ``{"choice": str|None, "free_text": str|None}``.

Visual treatment: a larger operational confirmation panel with visible option
descriptions. This prompt is used for permission and clarification moments, so
readability matters more than compactness.
"""

from qgis.PyQt.QtCore import Qt, QSize, pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

_SURFACE = "#1f1f1d"
_SURFACE_2 = "#262521"
_SURFACE_HOV = "#2d2b25"
_INPUT_BG = "#191918"
_BORDER = "#4a4234"
_BORDER_SOFT = "#343129"
_TEXT = "#eeeeea"
_TEXT_2 = "#bbb7ad"
_TEXT_3 = "#7d786d"
_ACCENT = "#e7dfcf"
_ACCENT_HOV = "#f2eadb"
_WARN = "#d99a3c"


def _mono(size, weight=QFont.Weight.Normal):
    font = QFont("JetBrains Mono", size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setWeight(weight)
    return font


class _OptionRow(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, label, description="", parent=None):
        super().__init__(parent)
        self._label = label or ""
        self.setObjectName("AskUserOptionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._hovered = False
        self._pressed = False
        self._apply_style()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
        self._layout = layout

        title = QLabel(self._label)
        self._title = title
        title.setObjectName("AskUserOptionTitle")
        title.setFont(_mono(11, QFont.Weight.DemiBold))
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        title.setStyleSheet(
            f"color:{_TEXT}; background:transparent; border:none; font-size:12px;"
        )
        layout.addWidget(title)

        if description:
            desc = QLabel(description)
            self._description = desc
            desc.setObjectName("AskUserOptionDescription")
            desc.setFont(_mono(10))
            desc.setWordWrap(True)
            desc.setMinimumWidth(0)
            desc.setStyleSheet(
                f"color:{_TEXT_2}; background:transparent; border:none; "
                f"font-size:11px; line-height:1.35;"
            )
            layout.addWidget(desc)
        else:
            self._description = None
        self.setMinimumHeight(self.heightForWidth(360))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        left, top, right, bottom = self._layout.getContentsMargins()
        inner_w = max(80, width - left - right)
        title_h = self._title.heightForWidth(inner_w)
        if title_h < 0:
            title_h = self._title.sizeHint().height()
        total = top + title_h + bottom
        if self._description is not None:
            desc_h = self._description.heightForWidth(inner_w)
            if desc_h < 0:
                desc_h = self._description.sizeHint().height()
            total += self._layout.spacing() + desc_h
        return max(48, total)

    def sizeHint(self):
        width = self.width() if self.width() > 0 else 360
        base = super().sizeHint()
        return QSize(base.width(), self.heightForWidth(width))

    def _apply_style(self):
        bg = _SURFACE_HOV if self._hovered else _SURFACE_2
        border = _WARN if self.hasFocus() else _BORDER_SOFT
        if self._pressed:
            bg = _ACCENT
            border = _ACCENT
        self.setStyleSheet(f"""
            QFrame#AskUserOptionRow {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 7px;
            }}
        """)

    def enterEvent(self, event):
        self._hovered = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        self._apply_style()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self._apply_style()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._pressed = False
        self._apply_style()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._apply_style()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        should_emit = self._pressed and self.rect().contains(event.pos())
        self._pressed = False
        self._apply_style()
        if should_emit:
            self.clicked.emit(self._label)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._label)
            event.accept()
            return
        super().keyPressEvent(event)


class AskUserCard(QFrame):
    submitted = pyqtSignal(object)

    def __init__(self, question, options, allow_free_text=True, parent=None):
        super().__init__(parent)
        self.setObjectName("AskUserCard")
        self.setMinimumWidth(280)
        self.setMaximumWidth(560)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(f"""
            QFrame#AskUserCard {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 8px;
            }}
        """)
        self._options = list(options)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        marker = QLabel("")
        marker.setFixedSize(9, 9)
        marker.setStyleSheet(
            f"background:{_WARN}; border:1px solid {_WARN}; border-radius:4px;"
        )
        header_row.addWidget(marker, 0, Qt.AlignmentFlag.AlignVCenter)

        header = QLabel("Action required")
        header.setFont(_mono(10, QFont.Weight.DemiBold))
        header.setStyleSheet(
            f"color:{_TEXT_2}; font-size:11px;"
            f"background:transparent; border:none;"
        )
        header_row.addWidget(header)
        header_row.addStretch(1)
        outer.addLayout(header_row)

        q = QLabel(question)
        q.setObjectName("AskUserQuestion")
        q.setFont(_mono(12, QFont.Weight.DemiBold))
        q.setWordWrap(True)
        q.setMinimumWidth(0)
        q.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        q.setStyleSheet(
            f"color:{_TEXT}; font-size:13px; line-height:1.45; "
            f"background:transparent; border:none;"
        )
        outer.addWidget(q)

        opt_col = QVBoxLayout()
        opt_col.setContentsMargins(0, 0, 0, 0)
        opt_col.setSpacing(8)
        for opt in self._options:
            label = opt.get("label", "")
            row = _OptionRow(label, opt.get("description", ""), self)
            row.clicked.connect(self._on_option)
            opt_col.addWidget(row)
        outer.addLayout(opt_col)

        if allow_free_text:
            ft_row = QHBoxLayout()
            ft_row.setContentsMargins(0, 0, 0, 0)
            ft_row.setSpacing(8)
            self._free_text = QLineEdit()
            self._free_text.setPlaceholderText("Or type your own answer\u2026")
            self._free_text.setMinimumHeight(36)
            self._free_text.setFont(_mono(10))
            self._free_text.setStyleSheet(f"""
                QLineEdit {{
                    background-color: {_INPUT_BG};
                    color: {_TEXT};
                    border: 1px solid {_BORDER_SOFT};
                    border-radius: 7px;
                    padding: 7px 10px;
                    font-size: 12px;
                    selection-background-color: {_TEXT};
                    selection-color: {_SURFACE};
                }}
                QLineEdit:focus {{
                    border-color: {_WARN};
                }}
            """)
            self._free_text.returnPressed.connect(self._on_free_text)
            ft_row.addWidget(self._free_text, 1)

            send = QPushButton("Send")
            send.setCursor(Qt.CursorShape.PointingHandCursor)
            send.setMinimumHeight(36)
            send.setMinimumWidth(72)
            send.setFont(_mono(10, QFont.Weight.DemiBold))
            send.setStyleSheet(f"""
                QPushButton {{
                    background-color: {_ACCENT};
                    color: {_SURFACE};
                    border: none;
                    border-radius: 7px;
                    padding: 7px 12px;
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

    def sizeHint(self):
        size = super().sizeHint()
        return QSize(max(420, size.width()), size.height())

    def _on_option(self, label):
        self.submitted.emit({"choice": label, "free_text": None})

    def _on_free_text(self):
        if self._free_text is None:
            return
        text = self._free_text.text().strip()
        if not text:
            return
        self.submitted.emit({"choice": None, "free_text": text})
