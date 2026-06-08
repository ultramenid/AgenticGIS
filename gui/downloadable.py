"""Reusable hover-to-download affordance for chat response widgets.

A small ``⤓`` button is overlaid on the top-right of a host widget and shown
only while the cursor is over the host (or the button itself). Clicking it runs
the host's save callback. Save helpers cover the native format per content type:
text → Markdown, chart/stats widgets → PNG / CSV, GIF → file copy.

Stdlib + PyQGIS only.
"""

import csv
import os
import shutil

from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox, QToolButton

_BTN_STYLE = """
    QToolButton {
        background-color: #1e1e1e;
        color: #a0a0a0;
        border: 1px solid #2e2e2e;
        border-radius: 4px;
        font-size: 13px;
        padding: 0px;
    }
    QToolButton:hover {
        color: #ececec;
        border-color: #3a3a3a;
    }
"""


class HoverDownloadButton(QToolButton):
    """A ``⤓`` button overlaid top-right of ``host``, visible on hover.

    ``on_click`` is a no-argument callable invoked when the button is pressed.
    The button is an absolutely-positioned child (not part of any layout), so
    it never affects the host's geometry.
    """

    def __init__(self, host, on_click, tooltip="Download"):
        super().__init__(host)
        self._host = host
        self.setText("⤓")  # ⤓ downwards arrow to bar
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(_BTN_STYLE)
        self.setFixedSize(22, 22)
        self.clicked.connect(lambda: on_click())
        self.hide()
        host.installEventFilter(self)
        self._reposition()

    def eventFilter(self, obj, event):
        if obj is self._host:
            etype = event.type()
            if etype == QEvent.Type.Enter:
                self._reposition()
                self.show()
                self.raise_()
            elif etype == QEvent.Type.Leave:
                # Keep visible if the cursor merely moved onto the button.
                pt = self._host.mapFromGlobal(QCursor.pos())
                if not self.geometry().contains(pt):
                    self.hide()
            elif etype == QEvent.Type.Resize:
                self._reposition()
        return False

    def _reposition(self):
        margin = 6
        x = max(0, self._host.width() - self.width() - margin)
        self.move(x, margin)


# --------------------------------------------------------------------------- #
# Save helpers — each opens a Save dialog and writes the chosen format.        #
# --------------------------------------------------------------------------- #

def _ask_path(parent, default_name, file_filter):
    path, _ = QFileDialog.getSaveFileName(parent, "Save", default_name, file_filter)
    return path


def _safe_name(text, fallback, suffix):
    """Build a filesystem-friendly default filename from arbitrary text."""
    base = "".join(c if (c.isalnum() or c in " -_") else "_" for c in (text or "")).strip()
    base = base.replace(" ", "_") or fallback
    return f"{base[:60]}{suffix}"


def save_text(parent, text, default_name="response.md"):
    """Save markdown/plain text to a .md (or .txt) file."""
    path = _ask_path(parent, default_name, "Markdown (*.md);;Text (*.txt)")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text or "")
    except OSError as exc:
        QMessageBox.warning(parent, "Save failed", str(exc))


def save_widget_png(parent, widget, default_name="chart.png"):
    """Snapshot a widget to a PNG image."""
    path = _ask_path(parent, default_name, "PNG image (*.png)")
    if not path:
        return
    if not path.lower().endswith(".png"):
        path += ".png"
    # Hide any overlay download buttons so they are not baked into the snapshot.
    overlays = [b for b in widget.findChildren(HoverDownloadButton) if b.isVisible()]
    for btn in overlays:
        btn.hide()
    try:
        pixmap = widget.grab()
    finally:
        for btn in overlays:
            btn.show()
    if not pixmap.save(path, "PNG"):
        QMessageBox.warning(parent, "Save failed", "Could not write PNG image.")


def save_csv(parent, rows, default_name="data.csv"):
    """Save an iterable of row-iterables as CSV."""
    path = _ask_path(parent, default_name, "CSV (*.csv)")
    if not path:
        return
    try:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            for row in rows:
                writer.writerow(row)
    except OSError as exc:
        QMessageBox.warning(parent, "Save failed", str(exc))


def save_file_copy(parent, src_path, default_name, file_filter):
    """Copy an existing file (e.g. a temp GIF) to a user-chosen location."""
    if not src_path or not os.path.exists(src_path):
        QMessageBox.warning(
            parent, "Save failed", "The source file is no longer available."
        )
        return
    path = _ask_path(parent, default_name, file_filter)
    if not path:
        return
    try:
        shutil.copyfile(src_path, path)
    except OSError as exc:
        QMessageBox.warning(parent, "Save failed", str(exc))
