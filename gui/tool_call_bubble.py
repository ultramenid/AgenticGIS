import json
import html

from qgis.PyQt.QtCore import Qt, QTimer, QSize
from qgis.PyQt.QtGui import QFont, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSizePolicy, QWidget,
)

# Design tokens - Monochrome minimal
_SURFACE     = "#1a1a1a"
_INPUT_BG    = "#222222"
_BORDER      = "#3a3a3a"
_BORDER_SOFT = "#2a2a2a"
_TEXT        = "#f0f0f0"
_TEXT_2      = "#b0b0b0"
_TEXT_3      = "#8a8a8a"
_DANGER      = "#ff6b6b"
_SUCCESS     = "#86efac"

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class ToolCallBubble(QFrame):
    """Single widget that shows a tool call lifecycle: Running → Done or Error."""

    def __init__(self, tool_name: str, tool_input: dict, parent=None):
        super().__init__(parent)
        self._tool_name = tool_name
        self._tool_input = tool_input
        self._dots_index = 0
        self._content_visible = False

        self.setObjectName("ToolCallBubble")
        self.setStyleSheet(
            f"QFrame#ToolCallBubble {{"
            f"  background: {_SURFACE};"
            f"  border: 1px solid {_BORDER_SOFT};"
            f"  border-radius: 10px;"
            f"}}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        # ── Outer layout ──────────────────────────────────────────────────────
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 8)
        outer.setSpacing(0)

        # ── Header row ────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(8)

        self.status_label = QLabel(_SPINNER_FRAMES[0])
        self.status_label.setStyleSheet(f"color: {_TEXT_3}; font-size: 10px;")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header.addWidget(self.status_label)

        mono_font = QFont("Consolas", 10)
        mono_font.setStyleHint(QFont.StyleHint.Monospace)

        self.name_label = QLabel(tool_name)
        self.name_label.setFont(mono_font)
        self.name_label.setStyleSheet(f"color: {_TEXT_2};")
        header.addWidget(self.name_label)

        header.addStretch()

        self.state_label = QLabel("processing")
        self.state_label.setStyleSheet(f"color: {_TEXT_3}; font-size: 10px;")
        header.addWidget(self.state_label)

        self.dots_label = QLabel("")
        self.dots_label.setStyleSheet(f"color: {_TEXT_3}; font-size: 10px;")
        header.addWidget(self.dots_label)

        self.toggle_btn = QPushButton("Details")
        self.toggle_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background: transparent;"
            f"  color: {_TEXT_3};"
            f"  font-size: 10px;"
            f"  border: none;"
            f"  padding: 0 4px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  color: {_TEXT_2};"
            f"}}"
        )
        self.toggle_btn.setVisible(False)
        self.toggle_btn.clicked.connect(self._toggle)
        header.addWidget(self.toggle_btn)

        outer.addLayout(header)

        # ── Collapsible content ───────────────────────────────────────────────
        self.content_widget = QWidget()
        self.content_widget.setVisible(False)
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 8, 0, 0)
        content_layout.setSpacing(6)
        outer.addWidget(self.content_widget)

        # ── Dots animation timer ──────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._animate_dots)
        self._timer.start()
        self.destroyed.connect(self._on_destroyed)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_result(self, result_str: str, is_error: bool = False) -> None:
        """Transition widget to Done or Error state with result content."""
        try:
            self._timer.stop()
            self.dots_label.setVisible(False)

            if is_error:
                self.state_label.setText("Error")
                self.status_label.setText("!")
                self.status_label.setStyleSheet(f"color: {_DANGER}; font-size: 10px;")
                self.setStyleSheet(
                    f"QFrame#ToolCallBubble {{"
                    f"  background: #1a0a0a;"
                    f"  border: 1px solid #7f1d1d;"
                    f"  border-radius: 10px;"
                    f"}}"
                )
            else:
                self.state_label.setText("Done")
                self.status_label.setText("✓")
                self.status_label.setStyleSheet(f"color: {_SUCCESS}; font-size: 10px;")

            self.toggle_btn.setVisible(True)

            # Build collapsible content
            try:
                content_layout = self.content_widget.layout()

                mono_style = (
                    f"background: {_INPUT_BG};"
                    f"color: {_TEXT_2};"
                    f"border-radius: 4px;"
                    f"padding: 8px;"
                    f"font-family: Consolas, monospace;"
                    f"font-size: 10px;"
                )

                if self._tool_input:
                    try:
                        raw_input = json.dumps(self._tool_input, indent=2)
                        truncated_input = raw_input[:500] + ("…" if len(raw_input) > 500 else "")
                        input_label = QLabel(truncated_input)
                        input_label.setStyleSheet(mono_style)
                        input_label.setWordWrap(True)
                        input_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                        content_layout.addWidget(input_label)
                    except Exception:
                        pass

                truncated_result = result_str[:800] + ("…" if len(result_str) > 800 else "")
                result_label = QLabel(truncated_result)
                result_label.setStyleSheet(mono_style)
                result_label.setWordWrap(True)
                result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                content_layout.addWidget(result_label)

                copy_btn = QPushButton("Copy")
                copy_btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  background: transparent;"
                    f"  color: {_TEXT_3};"
                    f"  font-size: 10px;"
                    f"  border: 1px solid {_BORDER};"
                    f"  border-radius: 4px;"
                    f"  padding: 2px 8px;"
                    f"}}"
                    f"QPushButton:hover {{"
                    f"  color: {_TEXT_2};"
                    f"}}"
                )
                copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(result_str))
                content_layout.addWidget(copy_btn, alignment=Qt.AlignmentFlag.AlignLeft)

                self.updateGeometry()
            except Exception:
                pass
        except Exception:
            pass

    # ── Private helpers ───────────────────────────────────────────────────────

    def _on_destroyed(self) -> None:
        try:
            self._timer.stop()
        except RuntimeError:
            pass

    def _toggle(self) -> None:
        self._content_visible = not self._content_visible
        self.content_widget.setVisible(self._content_visible)
        self.toggle_btn.setText("Hide" if self._content_visible else "Details")
        self.updateGeometry()

    def _animate_dots(self) -> None:
        try:
            self._dots_index = (self._dots_index + 1) % len(_SPINNER_FRAMES)
            self.status_label.setText(_SPINNER_FRAMES[self._dots_index])
        except RuntimeError:
            self._timer.stop()
