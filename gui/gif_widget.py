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

        # Extract name, gif_path, and per-frame labels from result dict
        name = data.get("name") or "Animation"
        gif_path = data.get("gif_path")
        self._gif_path = gif_path
        self._name = name
        # Optional per-frame captions (e.g. ["2020","2021",...]), one per frame
        # in playback order. Overlaid on the animation, synced to the frame.
        self._frame_labels = [str(x) for x in (data.get("frame_labels") or [])]
        self._label_overlay = None

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
        self._animation_label = animation_label

        # Try to load and play the GIF
        if gif_path and os.path.exists(gif_path):
            movie = QMovie(gif_path)
            if movie.isValid():
                animation_label.setMovie(movie)
                # Keep a reference to prevent garbage collection
                self._movie = movie
                if self._frame_labels:
                    self._build_label_overlay(animation_label)
                    movie.frameChanged.connect(self._on_frame_changed)
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

    def _build_label_overlay(self, host):
        """Create the per-frame caption overlaid on the animation."""
        overlay = QLabel(host)
        overlay.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160); color: #ffffff;"
            " font-size: 14px; font-weight: 600; padding: 2px 8px;"
            " border-radius: 4px;"
        )
        overlay.setText(self._frame_labels[0])
        overlay.adjustSize()
        overlay.show()
        self._label_overlay = overlay
        self._position_label_overlay()

    def _on_frame_changed(self, idx):
        if not self._label_overlay or not self._frame_labels:
            return
        if idx < 0:
            idx = 0
        elif idx >= len(self._frame_labels):
            idx = len(self._frame_labels) - 1
        self._label_overlay.setText(self._frame_labels[idx])
        self._label_overlay.adjustSize()
        self._position_label_overlay()

    def _position_label_overlay(self):
        overlay = self._label_overlay
        host = getattr(self, "_animation_label", None)
        if overlay is None or host is None:
            return
        margin = 8
        x = max(0, host.width() - overlay.width() - margin)
        y = max(0, host.height() - overlay.height() - margin)
        overlay.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_label_overlay()

    def _save(self):
        # Prefer Pillow-baked GIF with labels; fall back to original file copy.
        baked = self._bake_labels_to_gif()
        if baked and os.path.exists(baked):
            save_file_copy(
                self,
                baked,
                _safe_name(self._name, "animation", ".gif"),
                "GIF (*.gif)",
            )
            try:
                os.remove(baked)
            except OSError:
                pass
            return
        # Pillow unavailable or failed — save the original GIF without labels.
        save_file_copy(
            self,
            self._gif_path,
            _safe_name(self._name, "animation", ".gif"),
            "GIF (*.gif)",
        )

    # ── Optional Pillow label-burning ──────────────────────────────────────

    def _bake_labels_to_gif(self):
        """Re-encode the GIF so per-frame labels are burned into every frame.

        Returns the path of a temporary baked GIF, or None if Pillow is not
        available or the bake fails.  The caller is responsible for deleting the
        temp file after copying it to the user's chosen location.
        """
        if not self._gif_path or not self._frame_labels:
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            return None
        gif_path = self._gif_path
        labels = self._frame_labels
        try:
            img = Image.open(gif_path)
        except Exception:
            return None
        frames = []
        durations = []
        frame_idx = 0
        while True:
            try:
                # Copy frame so we can modify it
                frame = img.copy()
            except Exception:
                break
            # Convert palette images to RGB for text drawing
            if frame.mode in ("P", "L", "1"):
                frame = frame.convert("RGB")
            # Draw label for this frame (loop if fewer labels than frames)
            label = labels[min(frame_idx, len(labels) - 1)] if labels else ""
            if label:
                draw = ImageDraw.Draw(frame)
                # Use a system font if available; fall back to default bitmap
                try:
                    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
                except Exception:
                    try:
                        font = ImageFont.truetype("arial.ttf", 18)
                    except Exception:
                        font = ImageFont.load_default()
                # Measure text and position bottom-right with padding
                bbox = draw.textbbox((0, 0), label, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                pad = 8
                x = max(0, frame.width - tw - pad)
                y = max(0, frame.height - th - pad)
                # Dark translucent background pill behind text
                pill = (
                    max(0, x - 4),
                    max(0, y - 2),
                    min(frame.width, x + tw + 4),
                    min(frame.height, y + th + 4),
                )
                draw.rectangle(pill, fill=(0, 0, 0, 160))
                draw.text((x, y), label, fill=(255, 255, 255, 255), font=font)
            # Convert back to palette for GIF
            frames.append(frame.convert("P", palette=Image.ADAPTIVE))
            # Duration in ms (default 100 ms)
            dur = img.info.get("duration", 100)
            durations.append(dur if dur is not None else 100)
            frame_idx += 1
            try:
                img.seek(frame_idx)
            except EOFError:
                break
        if not frames:
            return None
        # Save baked GIF to a temp file next to the original
        import tempfile
        fd, tmp_path = tempfile.mkstemp(
            suffix="_labeled.gif", dir=os.path.dirname(gif_path)
        )
        os.close(fd)
        try:
            frames[0].save(
                tmp_path,
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=img.info.get("loop", 0),
                optimize=False,
            )
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return None
        return tmp_path
