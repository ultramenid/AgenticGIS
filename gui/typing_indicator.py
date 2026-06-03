"""Typing indicator — animated wave dots, minimal dark style.

Anti-AI-SLOP: no emoji, no heavy icons, pure typography.
"""

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

# Design tokens (must match chat_dock.py)
_INPUT_BG    = "#1e1e1e"
_BORDER      = "#2e2e2e"
_TEXT        = "#ececec"
_TEXT_2      = "#a0a0a0"

# Wave frames: filled dot = active, circle = inactive
_WAVE_FRAMES = [
    "●○○",  # ●○○
    "●●○",  # ●●○
    "●●●",  # ●●●
    "●●○",  # ●●○  (reverse, creates wave feel)
]


class TypingIndicator(QWidget):
    """Animated wave-dot indicator displayed while the agent is working."""

    def __init__(self, text="AgenticGIS", parent=None):
        super().__init__(parent)
        self.base_text = text
        self._frame = 0
        self._setup_ui()
        # Belt-and-suspenders: stop timer if the widget is destroyed
        # (covers the case where deleteLater fires before stop() is called)
        self.destroyed.connect(self._on_destroyed)

    def _setup_ui(self):
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 0, 16, 0)
        outer.setSpacing(0)

        bubble = QWidget()
        bubble.setObjectName("typingBubble")
        bubble.setStyleSheet(f"""
            QWidget#typingBubble {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 12px;
            }}
        """)
        bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        layout = QHBoxLayout(bubble)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(6)

        # Prefix label — primary text color
        self.prefix_label = QLabel(self.base_text)
        font = QFont()
        font.setFamily("Inter")
        font.setPointSize(13)
        font.setStyleHint(QFont.SansSerif)
        self.prefix_label.setFont(font)
        self.prefix_label.setStyleSheet(f"""
            color: {_TEXT};
            background: transparent;
            border: none;
            font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
        """)
        layout.addWidget(self.prefix_label)

        # Dot animation label — secondary text color
        self.dots_label = QLabel(_WAVE_FRAMES[0])
        dots_font = QFont()
        dots_font.setFamily("Inter")
        dots_font.setPointSize(10)
        dots_font.setStyleHint(QFont.SansSerif)
        self.dots_label.setFont(dots_font)
        self.dots_label.setStyleSheet(f"""
            color: {_TEXT_2};
            background: transparent;
            border: none;
            letter-spacing: 2px;
        """)
        layout.addWidget(self.dots_label)
        layout.addStretch(1)

        outer.addWidget(bubble)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_frame)
        self._timer.start(380)

    def _update_frame(self):
        self._frame = (self._frame + 1) % len(_WAVE_FRAMES)
        self.dots_label.setText(_WAVE_FRAMES[self._frame])

    def set_text(self, text):
        self.base_text = text
        self.prefix_label.setText(text)
        self._frame = 0
        self.dots_label.setText(_WAVE_FRAMES[0])

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
