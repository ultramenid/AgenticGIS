"""GIF widget — dark palette, animated inline display.

Renders animated GIFs embedded directly in the chat transcript.
Matches ChartWidget styling and design tokens.
"""

import os
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QMovie
from qgis.PyQt.QtWidgets import (
    QFrame, QVBoxLayout, QLabel
)

from .downloadable import HoverDownloadButton, save_file_copy, _safe_name

# Design tokens — match ChartWidget
_SURFACE = "#161616"
_BORDER = "#2e2e2e"
_TEXT = "#ececec"
_TEXT_2 = "#a0a0a0"


class GifWidget(QFrame):
    """Renders an animated GIF inline in the chat transcript.

    Reads gif_path and name from the tool result dict; applies dark styling
    to match ChartWidget. Falls back gracefully if the GIF is missing or
    invalid.
    """

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"""
            GifWidget {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 8px;
            }}
        """)
        self.setMaximumWidth(600)

        # Extract name and gif_path from result dict
        name = data.get("name") or "Animation"
        gif_path = data.get("gif_path")
        self._gif_path = gif_path
        self._name = name

        # Layout: title label + animation label
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Title label (muted)
        title_label = QLabel(name)
        title_label.setStyleSheet(f"color: {_TEXT_2}; font-size: 12px;")

        # Animation label
        animation_label = QLabel()
        animation_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        animation_label.setMinimumHeight(200)
        animation_label.setStyleSheet(
            f"background-color: {_SURFACE}; color: {_TEXT};"
        )

        # Try to load and play the GIF
        if gif_path and os.path.exists(gif_path):
            movie = QMovie(gif_path)
            if movie.isValid():
                animation_label.setMovie(movie)
                # Keep a reference to prevent garbage collection
                self._movie = movie
                movie.start()
            else:
                # GIF file exists but is not valid
                animation_label.setText("Animation no longer available.")
                self._movie = None
        else:
            # GIF path is missing, None, or file does not exist
            animation_label.setText("Animation no longer available.")
            self._movie = None

        layout.addWidget(title_label)
        layout.addWidget(animation_label)
        layout.addStretch()
        self.setLayout(layout)

        # Hover-to-download: save the animated GIF to disk.
        HoverDownloadButton(self, self._save, tooltip="Save GIF")

    def _save(self):
        save_file_copy(
            self,
            self._gif_path,
            _safe_name(self._name, "animation", ".gif"),
            "GIF (*.gif)",
        )
