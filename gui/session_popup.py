"""Sessions popup — searchable list anchored under the dock's Sessions button."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from .formatting import format_relative_time
from .theme import (
    fs,
    sans_family,
    DOCK_SURFACE as _SURFACE,
    DOCK_SURFACE_2 as _SURFACE_2,
    DOCK_BORDER as _BORDER,
    DOCK_TEXT as _TEXT,
    DOCK_TEXT_3 as _TEXT_3,
    DOCK_WARN as _WARN,
    DOCK_BLUE as _BLUE,
    DOCK_DANGER as _DANGER,
)


class _SessionRow(QFrame):
    """One clickable session: name, meta line, and hover-revealed rename/delete buttons."""

    def __init__(self, session, active, meta, on_open, on_rename, on_delete, parent=None):
        super().__init__(parent)
        self.name = session.get("name", "")
        self._on_open = on_open
        self.setObjectName("SessionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        bg = _SURFACE_2 if active else "transparent"
        accent = _BLUE if active else "transparent"
        self.setStyleSheet(f"""
            QFrame#SessionRow {{
                background:{bg}; border:none; border-left:2px solid {accent}; border-radius:8px;
            }}
            QFrame#SessionRow:hover {{ background:{_SURFACE_2}; }}
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 6, 7)
        row.setSpacing(6)

        col = QVBoxLayout()
        col.setSpacing(2)
        title = QLabel(self.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setMinimumWidth(1)  # let long names clip instead of widening the popup
        title.setStyleSheet(
            f"color:{_TEXT}; background:transparent; font-family:{sans_family()};"
            f" font-size:{fs(12.5)}px; font-weight:600;"
        )
        sub = QLabel(meta)
        sub.setTextFormat(Qt.TextFormat.PlainText)
        sub.setMinimumWidth(1)
        color = _BLUE if active else (_WARN if session.get("size_warning") else _TEXT_3)
        sub.setStyleSheet(
            f"color:{color}; background:transparent;"
            f" font-family:{sans_family()}; font-size:{fs(11)}px;"
        )
        col.addWidget(title)
        col.addWidget(sub)
        row.addLayout(col, 1)

        self._actions = []
        for glyph, tip, hover, handler in (
            ("✎", "Rename session", _TEXT, on_rename),
            ("✕", "Delete session", _DANGER, on_delete),
        ):
            btn = QToolButton()
            btn.setText(glyph)
            btn.setToolTip(tip)
            btn.setFixedSize(24, 24)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QToolButton {{ color:{_TEXT_3}; background:transparent; border:none;"
                f" border-radius:6px; font-size:{fs(12)}px; }}"
                f"QToolButton:hover {{ color:{hover}; background:{_SURFACE}; }}"
            )
            btn.clicked.connect(lambda _checked=False, h=handler: h())
            policy = btn.sizePolicy()
            policy.setRetainSizeWhenHidden(True)  # no layout jump on hover
            btn.setSizePolicy(policy)
            btn.setVisible(False)
            self._actions.append(btn)
            row.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def _show_actions(self, visible):
        for btn in self._actions:
            btn.setVisible(visible)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._show_actions(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._show_actions(False)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_open()


class SessionPopup(QFrame):
    """Frameless popup: search, "+ New", and recent sessions newest first."""

    def __init__(self, sessions, active_id, on_open, on_new, on_rename, on_delete,
                 format_size, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("SessionPopup")
        self.setStyleSheet(f"""
            QFrame#SessionPopup {{
                background:{_SURFACE}; border:1px solid {_BORDER}; border-radius:12px;
            }}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sessions…")
        self.search.setStyleSheet(f"""
            QLineEdit {{
                background:{_SURFACE_2}; color:{_TEXT}; border:1px solid {_BORDER};
                border-radius:7px; padding:5px 8px; font-family:{sans_family()}; font-size:{fs(12)}px;
            }}
        """)
        top.addWidget(self.search, 1)
        new_btn = QPushButton("+ New")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                color:{_TEXT}; background:{_SURFACE_2}; border:1px solid {_BORDER};
                border-radius:7px; padding:5px 10px; font-size:{fs(12)}px;
            }}
            QPushButton:hover {{ border-color:{_TEXT_3}; }}
        """)
        new_btn.clicked.connect(lambda: self._then(on_new))
        top.addWidget(new_btn)
        outer.addLayout(top)

        body = QWidget()
        body.setStyleSheet("background:transparent;")
        self._list = QVBoxLayout(body)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        self._rows = []
        ordered = sorted(sessions, key=lambda s: str(s.get("updated_at") or ""), reverse=True)
        for session in ordered:
            sid = session["id"]
            active = sid == active_id
            when = format_relative_time(session.get("updated_at"))
            bits = ["Current" if active else "", when, format_size(session.get("size_bytes", 0))]
            row = _SessionRow(
                session, active, "  ·  ".join(b for b in bits if b),
                on_open=lambda sid=sid: self._then(on_open, sid),
                on_rename=lambda sid=sid: self._then(on_rename, sid),
                on_delete=lambda sid=sid: self._then(on_delete, sid),
            )
            self._rows.append(row)
            self._list.addWidget(row)
        self._empty = QLabel("No matching sessions")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet(f"color:{_TEXT_3}; background:transparent; padding:12px; font-size:{fs(11)}px;")
        self._empty.setVisible(not self._rows)
        self._list.addWidget(self._empty)
        self._list.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ border:none; background:transparent; }}
            QScrollBar:vertical {{ background:transparent; width:6px; }}
            QScrollBar::handle:vertical {{ background:{_BORDER}; border-radius:3px; min-height:24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        scroll.setWidget(body)
        scroll.setMaximumHeight(380)
        scroll.setMinimumHeight(min(380, max(60, 52 * len(self._rows))))
        outer.addWidget(scroll)

        hint = QLabel("Latest 20 sessions are kept")
        hint.setStyleSheet(f"color:{_TEXT_3}; background:transparent; font-size:{fs(10)}px; padding:0 4px;")
        outer.addWidget(hint)

        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._open_first)
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def _then(self, fn, *args):
        """Close the popup first so follow-up dialogs aren't stacked under it."""
        self.close()
        fn(*args)

    def _filter(self, text):
        q = (text or "").strip().lower()
        shown = 0
        for row in self._rows:
            visible = q in row.name.lower()
            row.setVisible(visible)
            shown += visible
        self._empty.setVisible(shown == 0)

    def _open_first(self):
        for row in self._rows:
            if row.isVisible():
                row._on_open()
                return

    def show_below(self, anchor, width):
        self.setFixedWidth(width)
        self.adjustSize()
        pos = anchor.mapToGlobal(anchor.rect().bottomRight())
        pos.setX(pos.x() - width)
        pos.setY(pos.y() + 4)
        self.move(pos)
        self.show()
