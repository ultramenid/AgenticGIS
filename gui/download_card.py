"""Download card widget — shows a downloadable file from a tool result.

Rendered inline in the chat when a tool returns a result containing a file_path.
Displays filename, file type, and a download button.
"""

import os

from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from .downloadable import HoverDownloadButton, save_file_copy

_SURFACE = "#161616"
_BORDER = "#2e2e2e"
_TEXT = "#ececec"
_TEXT_2 = "#a0a0a0"
_ACCENT = "#e7dfcf"

_FILE_ICONS = {
    ".tif": "🗺", ".tiff": "🗺", ".geotiff": "🗺",
    ".shp": "📐", ".geojson": "📐", ".gpkg": "📐",
    ".csv": "📊", ".txt": "📄", ".md": "📄",
    ".json": "📋",
    ".gif": "🎬", ".png": "🖼", ".jpg": "🖼", ".jpeg": "🖼",
}


def _file_icon(path):
    ext = os.path.splitext(path)[-1].lower()
    return _FILE_ICONS.get(ext, "📦")


def _human_size(path):
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} TB"


class DownloadWidget(QFrame):
    """Card showing a downloadable file with filename, type icon, and size."""

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = data
        file_path = data.get("file_path") or data.get("download_path") or ""
        self._file_path = file_path

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"""
            DownloadWidget {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 8px;
            }}
        """)
        self.setMaximumWidth(600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # File info row
        info_row = QHBoxLayout()
        info_row.setSpacing(10)

        icon_label = QLabel(_file_icon(file_path))
        icon_label.setStyleSheet("font-size: 20px;")
        info_row.addWidget(icon_label)

        name_label = QLabel(os.path.basename(file_path) or "Download")
        name_label.setFont(QFont("JetBrains Mono", 11, QFont.Weight.DemiBold))
        name_label.setStyleSheet(
            f"color: {_TEXT}; font-size: 13px; background: transparent;"
        )
        name_label.setWordWrap(True)
        info_row.addWidget(name_label, 1)

        size_label = QLabel(_human_size(file_path))
        size_label.setStyleSheet(
            f"color: {_TEXT_2}; font-size: 11px; background: transparent;"
        )
        info_row.addWidget(size_label)

        layout.addLayout(info_row)

        # Description (optional)
        desc = data.get("description") or data.get("name") or ""
        if desc:
            desc_label = QLabel(desc)
            desc_label.setStyleSheet(
                f"color: {_TEXT_2}; font-size: 11px; background: transparent;"
            )
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)

        # Download button
        HoverDownloadButton(
            self, self._save,
            tooltip="Download file",
        )

    def _save(self):
        if not self._file_path or not os.path.exists(self._file_path):
            return
        name = os.path.basename(self._file_path)
        ext = os.path.splitext(name)[-1].lower()
        filter_map = {
            ".tif": "GeoTIFF (*.tif)", ".tiff": "GeoTIFF (*.tiff)",
            ".shp": "Shapefile (*.shp)", ".geojson": "GeoJSON (*.geojson)",
            ".gpkg": "GeoPackage (*.gpkg)", ".csv": "CSV (*.csv)",
            ".json": "JSON (*.json)", ".gif": "GIF (*.gif)",
            ".png": "PNG (*.png)", ".jpg": "JPEG (*.jpg)",
            ".txt": "Text (*.txt)", ".md": "Markdown (*.md)",
        }
        filt = filter_map.get(ext, f"File (*{ext})")
        save_file_copy(self, self._file_path, name, filt)
