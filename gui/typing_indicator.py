"""Typing indicator — blinking terminal cursor, minimal dark style.

Anti-AI-SLOP: no emoji, no heavy icons, pure typography.
"""

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget
from .theme import fs, ui_font, DOCK_TEXT_3 as _TEXT_3, DOCK_TEXT_4 as _TEXT_4

# Blink states: visible cursor vs blank
_CURSOR_ON = "▋"
_CURSOR_OFF = " "


class TypingIndicator(QWidget):
    """Blinking terminal-cursor indicator displayed while the agent is working."""

    def __init__(self, text="AgenticGIS", parent=None):
        super().__init__(parent)
        self.base_text = text
        self._cursor_visible = True
        self._setup_ui()
        # Belt-and-suspenders: stop timer if the widget is destroyed
        # (covers the case where deleteLater fires before stop() is called)
        self.destroyed.connect(self._on_destroyed)

    def _setup_ui(self):
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(18, 0, 16, 0)
        outer.setSpacing(0)

        bubble = QWidget()
        bubble.setObjectName("typingBubble")
        bubble.setStyleSheet("QWidget#typingBubble { background: transparent; border: none; }")
        bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QHBoxLayout(bubble)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        # Prefix label — primary text color
        self.prefix_label = QLabel(self.base_text)
        self.prefix_label.setFont(ui_font(13))
        self.prefix_label.setStyleSheet(
            f"color: {_TEXT_3}; background: transparent; border: none; font-size: {fs(13)}px;"
        )
        layout.addWidget(self.prefix_label)

        # Blinking terminal cursor label — dim monospace
        self.cursor_label = QLabel(_CURSOR_ON)
        self.cursor_label.setFont(ui_font(13))
        self.cursor_label.setMinimumWidth(12)
        self.cursor_label.setStyleSheet(
            f"color: {_TEXT_4}; background: transparent; border: none; font-size: {fs(13)}px;"
        )
        layout.addWidget(self.cursor_label)
        layout.addStretch(1)

        outer.addWidget(bubble)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_blink)
        self._timer.start(500)

    def _update_blink(self):
        self._cursor_visible = not self._cursor_visible
        self.cursor_label.setText(_CURSOR_ON if self._cursor_visible else _CURSOR_OFF)

    def set_text(self, text):
        self.base_text = text
        self.prefix_label.setText(text)
        self._cursor_visible = True
        self.cursor_label.setText(_CURSOR_ON)

    def stop(self):
        """Stop the animation timer. Safe to call multiple times."""
        if self._timer.isActive():
            self._timer.stop()

    def _on_destroyed(self):
        """Ensure timer is stopped when Qt destroys this object."""
        try:
            self._timer.stop()
        except RuntimeError:
            pass  # C++ object already deleted
