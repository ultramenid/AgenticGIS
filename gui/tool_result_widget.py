"""Collapsible tool result widget — matches dark palette."""

import html
import json

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont, QGuiApplication
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

# Design tokens (must match chat_dock.py)
_SURFACE     = "#131316"
_INPUT_BG    = "#1c1c20"
_BORDER      = "#27272a"
_BORDER_SOFT = "#1f1f23"
_TEXT        = "#fafafa"
_TEXT_2      = "#a1a1aa"
_TEXT_3      = "#71717a"
_DANGER      = "#ef4444"
_SUCCESS     = "#22c55e"

# Error variant colors — on-palette dark reds
_ERR_BG      = "#1c0a0a"
_ERR_BORDER  = "#7f1d1d"


class ToolResultWidget(QFrame):
    """A collapsible card showing a tool call and its result."""

    def __init__(self, tool_name, tool_input, tool_result, is_error=False, parent=None):
        super().__init__(parent)
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_result = tool_result
        self.is_error = is_error
        self._expanded = False  # collapsed by default
        self._build_ui()

    def _build_ui(self):
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        if self.is_error:
            bg_color     = _ERR_BG
            border_color = _ERR_BORDER
            accent_color = _DANGER
        else:
            bg_color     = _SURFACE
            border_color = _BORDER_SOFT
            accent_color = _SUCCESS

        self.setStyleSheet(f"""
            ToolResultWidget {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)

        # ── Header row ──────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(6)

        # Status dot
        status_dot = QLabel(
            f"<span style='color:{accent_color}; font-size:8px;'>&#9679;</span>"
        )
        status_dot.setTextFormat(Qt.RichText)
        status_dot.setStyleSheet("background: transparent;")
        header.addWidget(status_dot)

        # Tool name — monospace so function names render cleanly
        name_label = QLabel(html.escape(self.tool_name))
        mono_font = QFont("Consolas")
        mono_font.setStyleHint(QFont.Monospace)
        mono_font.setPointSize(10)
        name_label.setFont(mono_font)
        name_label.setStyleSheet(f"""
            color: {_TEXT_2};
            background: transparent;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 11px;
        """)
        header.addWidget(name_label)
        header.addStretch(1)

        # Toggle button
        self.toggle_btn = QPushButton("Details")
        self.toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {_TEXT_3};
                border: none;
                font-size: 10px;
                padding: 0 4px;
            }}
            QPushButton:hover {{ color: {_TEXT_2}; }}
        """)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle)
        header.addWidget(self.toggle_btn)

        layout.addLayout(header)

        # ── Collapsible content ─────────────────────────────────────────
        self.content_widget = QWidget()
        self.content_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.content_widget.setVisible(False)  # collapsed by default
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 8, 0, 0)
        content_layout.setSpacing(6)

        # Input section
        if self.tool_input:
            input_text = html.escape(
                json.dumps(self.tool_input, indent=2, default=str)
            )[:500]
            input_display = QLabel(
                f'<pre style="margin:0; white-space:pre-wrap;">{input_text}</pre>'
            )
            input_display.setStyleSheet(f"""
                background-color: {_INPUT_BG};
                color: {_TEXT_2};
                border-radius: 4px;
                padding: 8px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 10px;
            """)
            input_display.setTextFormat(Qt.RichText)
            input_display.setWordWrap(True)
            content_layout.addWidget(input_display)

        # Result section — header row with "Result:" label + copy button
        result_header = QHBoxLayout()
        result_header.setSpacing(4)
        result_header.setContentsMargins(0, 0, 0, 0)

        result_section_label = QLabel("Result")
        result_section_label.setStyleSheet(
            f"color: {_TEXT_3}; background: transparent; font-size: 10px;"
        )
        result_header.addWidget(result_section_label)
        result_header.addStretch(1)

        copy_btn = QPushButton("Copy")
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {_TEXT_3};
                border: 1px solid {_BORDER};
                border-radius: 3px;
                font-size: 9px;
                padding: 1px 6px;
            }}
            QPushButton:hover {{
                color: {_TEXT_2};
                border-color: {_TEXT_3};
            }}
            QPushButton:pressed {{
                color: {_TEXT};
            }}
        """)
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_result)
        result_header.addWidget(copy_btn)

        content_layout.addLayout(result_header)

        # Result display
        result_str = str(self.tool_result)
        truncated = len(result_str) > 800
        result_text = html.escape(result_str[:800])
        if truncated:
            result_text += f"\n<span style='color:{_TEXT_3};'>[truncated]</span>"
        result_display = QLabel(
            f'<pre style="margin:0; white-space:pre-wrap;">{result_text}</pre>'
        )
        result_display.setWordWrap(True)
        result_display.setStyleSheet(f"""
            background-color: {_INPUT_BG};
            color: {_TEXT_2};
            border-radius: 4px;
            padding: 8px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 10px;
        """)
        result_display.setTextFormat(Qt.RichText)
        content_layout.addWidget(result_display)

        layout.addWidget(self.content_widget)

    def _toggle(self):
        self._expanded = not self._expanded
        self.content_widget.setVisible(self._expanded)
        self.toggle_btn.setText("Hide" if self._expanded else "Details")

    def _copy_result(self):
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(self.tool_result))
