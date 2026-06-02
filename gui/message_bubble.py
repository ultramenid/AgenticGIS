"""Individual chat message bubble — minimal, full-width.

Bubbles span the full width of the transcript and are left- or
right-aligned inside it. No avatars, no shadows, soft rounded
surfaces in the project's dark-minimal palette.
"""

import html
import re

from qgis.PyQt.QtCore import Qt, QPropertyAnimation, QEasingCurve
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_SURFACE     = "#131316"
_CANVAS      = "#0a0a0b"
_INPUT_BG    = "#1c1c20"
_BORDER      = "#27272a"
_BORDER_SOFT = "#1f1f23"
_TEXT        = "#fafafa"
_TEXT_2      = "#a1a1aa"
_TEXT_3      = "#71717a"
_ACCENT      = "#fafafa"
_ACCENT_HOV  = "#e4e4e7"
_DANGER      = "#ef4444"
_SUCCESS     = "#22c55e"


def _render_md_table(match) -> str:
    """Convert a matched markdown table block to a styled HTML table."""
    raw = match.group(0)
    lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
    # Skip separator lines: rows whose non-pipe content is only -, :, space
    data_lines = [ln for ln in lines if not re.match(r'^[\s|:\-]+$', ln)]
    if not data_lines:
        return raw

    th_style = (
        f"padding:6px 10px; text-align:left; color:{_TEXT}; font-weight:600; "
        f"border-bottom:1px solid {_BORDER}; background:{_SURFACE}; white-space:nowrap;"
    )
    td_style = (
        f"padding:5px 10px; text-align:left; color:{_TEXT_2}; "
        f"border-bottom:1px solid {_BORDER_SOFT};"
    )
    table_style = (
        f"border-collapse:collapse; width:100%; margin:6px 0; "
        f"font-size:12px; font-family:'Consolas','Courier New',monospace; "
        f"background:{_INPUT_BG}; border:1px solid {_BORDER}; border-radius:6px;"
    )

    rows_html = []
    for i, ln in enumerate(data_lines):
        cells = [c.strip() for c in ln.split("|")]
        cells = [c for j, c in enumerate(cells) if c or (0 < j < len(cells) - 1)]
        if not cells:
            continue
        if i == 0:
            cells_html = "".join(f'<th style="{th_style}">{c}</th>' for c in cells)
        else:
            cells_html = "".join(f'<td style="{td_style}">{c}</td>' for c in cells)
        rows_html.append(f"<tr>{cells_html}</tr>")

    if not rows_html:
        return raw
    return f'<table style="{table_style}">{"".join(rows_html)}</table>'


def _md_to_html(text: str) -> str:
    """Convert a small subset of Markdown to HTML, safe for QLabel RichText.

    Processing order:
    1. html.escape the whole string so < > & are safe.
    2. Extract and replace code blocks (```...```) with placeholders to
       protect their content from further substitution.
    3. Apply inline patterns (bold, italic, inline code, headings, list items).
    4. Convert remaining newlines to <br>.
    5. Restore code block placeholders.
    """
    safe = html.escape(text)

    code_blocks = []

    def _save_code_block(m):
        body = m.group(2)
        rendered = (
            f'<pre style="background:{_SURFACE}; color:{_TEXT_2}; '
            f'border:1px solid {_BORDER}; border-radius:4px; padding:8px; '
            f'font-family:monospace; font-size:11px; white-space:pre-wrap; '
            f'margin:4px 0;">{body}</pre>'
        )
        placeholder = f"\x00CODE{len(code_blocks)}\x00"
        code_blocks.append(rendered)
        return placeholder

    safe = re.sub(r"```([^\n]*)\n(.*?)```", _save_code_block, safe, flags=re.DOTALL)

    safe = re.sub(
        r"(?m)^### (.+)$",
        lambda m: (
            f'<div style="font-size:12px; font-weight:600; color:{_TEXT_2}; '
            f'margin:3px 0 2px 0;">{m.group(1)}</div>'
        ),
        safe,
    )
    safe = re.sub(
        r"(?m)^## (.+)$",
        lambda m: (
            f'<div style="font-size:13px; font-weight:bold; color:{_ACCENT_HOV}; '
            f'margin:4px 0 2px 0;">{m.group(1)}</div>'
        ),
        safe,
    )
    safe = re.sub(
        r"(?m)^# (.+)$",
        lambda m: (
            f'<div style="font-size:15px; font-weight:bold; color:{_TEXT}; '
            f'margin:6px 0 3px 0;">{m.group(1)}</div>'
        ),
        safe,
    )

    safe = re.sub(
        r"(?m)^- (.+)$",
        lambda m: (
            f'<div style="padding-left:12px; color:{_TEXT};">• {m.group(1)}</div>'
        ),
        safe,
    )

    safe = re.sub(
        r"`([^`]+)`",
        lambda m: (
            f'<code style="background:{_SURFACE}; color:{_SUCCESS}; '
            f'border-radius:3px; padding:1px 4px; font-family:monospace; '
            f'font-size:12px;">{m.group(1)}</code>'
        ),
        safe,
    )

    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    # Markdown tables — must run before \n→<br> so row structure is intact
    safe = re.sub(r"(?m)(?:^\|[^\n]*\n){2,}(?:^\|[^\n]*)?", _render_md_table, safe)

    safe = safe.replace("\n", "<br>")

    for i, block in enumerate(code_blocks):
        safe = safe.replace(f"\x00CODE{i}\x00", block)

    return safe


def _md_inline(text: str) -> str:
    """Streaming path — inline markdown only, no fenced code blocks.

    Applies bold, italic, inline code, and bullet points.
    Fenced code blocks are intentionally skipped to avoid showing a half-rendered
    fence while the closing ``` has not arrived yet during streaming.
    """
    safe = html.escape(text)

    # Bullet list items
    safe = re.sub(
        r"(?m)^- (.+)$",
        lambda m: f'<div style="padding-left:12px; color:{_TEXT};">• {m.group(1)}</div>',
        safe,
    )

    # Inline code (backtick)
    safe = re.sub(
        r"`([^`\n]+)`",
        lambda m: (
            f'<code style="background:{_SURFACE}; color:{_SUCCESS}; '
            f'border-radius:3px; padding:1px 4px; font-family:monospace; '
            f'font-size:12px;">{m.group(1)}</code>'
        ),
        safe,
    )

    # Bold then italic
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    safe = safe.replace("\n", "<br>")
    return safe


class MessageBubble(QFrame):
    """A single message bubble with alignment and optional markdown rendering."""

    def __init__(
        self,
        text: str,
        sender_name: str = "",
        is_user: bool = False,
        is_error: bool = False,
        is_tool: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.text = text
        self.sender_name = sender_name
        self.is_user = is_user
        self.is_error = is_error
        self.is_tool = is_tool
        # Streaming optimization: keep last-processed state to avoid O(N²) md.
        self._last_text = ""
        self._last_html = ""
        self._build_ui()
        self._animate_entrance()

    def _build_ui(self):
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        if self.is_error:
            bg_color      = "#2a0f10"
            text_color    = "#fecaca"
            border_color  = "#7f1d1d"
            border_radius = "12px"
        elif self.is_tool:
            bg_color      = _SURFACE
            text_color    = _TEXT_2
            border_color  = _BORDER_SOFT
            border_radius = "12px"
        elif self.is_user:
            bg_color      = _ACCENT
            text_color    = _CANVAS
            border_color  = _ACCENT
            border_radius = "14px"
        else:
            bg_color      = _INPUT_BG
            text_color    = _TEXT
            border_color  = _BORDER
            border_radius = "12px"

        self.setStyleSheet(f"""
            MessageBubble {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: {border_radius};
                padding: 0px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(0)

        initial_html = html.escape(self.text) if self.text else ""

        self.text_label = QLabel(initial_html)
        self.text_label.setWordWrap(True)
        self.text_label.setTextFormat(Qt.RichText)
        self.text_label.setTextInteractionFlags(
            Qt.TextBrowserInteraction | Qt.TextSelectableByMouse
        )
        self.text_label.setOpenExternalLinks(True)

        font = QFont("Inter", 13)
        font.setStyleHint(QFont.SansSerif)
        self.text_label.setFont(font)
        self.text_label.setStyleSheet(f"""
            color: {text_color};
            background: transparent;
            border: none;
            font-family: 'Inter', 'Segoe UI', -apple-system, sans-serif;
        """)

        layout.addWidget(self.text_label)

    def _animate_entrance(self):
        # Only animate bubbles that have text at creation (user msgs, errors, tool msgs).
        # Streaming agent bubbles start empty — skip animation to avoid flicker during
        # token delivery and the double-fade when finalize_text is called.
        if not self.text:
            return

        opacity_effect = QGraphicsOpacityEffect(self)
        opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(opacity_effect)

        self._entrance_anim = QPropertyAnimation(opacity_effect, b"opacity", self)
        self._entrance_anim.setDuration(160)
        self._entrance_anim.setStartValue(0.0)
        self._entrance_anim.setEndValue(1.0)
        self._entrance_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._entrance_anim.start(QPropertyAnimation.DeleteWhenStopped)

    def set_text(self, text: str):
        self.text = text
        self.text_label.setText(html.escape(text))

    def set_streaming_text(self, text: str):
        """Streaming path — delta-only markdown + cursor.

        Instead of re-parsing the full text on every token (O(N²)), we only
        process the newly arrived delta, then append it to the cached HTML.
        """
        delta = text[len(self._last_text):]
        self._last_text = text
        cursor = f'<span style="color:{_TEXT_2};">▋</span>'

        if not delta:
            # No new text — just refresh cursor position
            self.text_label.setText(self._last_html + cursor)
            return

        # Markdown-process only the delta.  We escape it then run the same
        # inline transforms _md_inline uses so bold/italic/code/bullets work.
        html_delta = html.escape(delta)

        def _inline_transforms(chunk: str) -> str:
            # Bullet list items (line-start only)
            chunk = re.sub(
                r"(?m)^- (.+)$",
                lambda m: (
                    f'<div style="padding-left:12px; color:{_TEXT};">'
                    f'• {m.group(1)}</div>'
                ),
                chunk,
            )
            # Inline code
            chunk = re.sub(
                r"`([^`\n]+)`",
                lambda m: (
                    f'<code style="background:{_SURFACE}; color:{_SUCCESS}; '
                    f'border-radius:3px; padding:1px 4px; font-family:monospace; '
                    f'font-size:12px;">{m.group(1)}</code>'
                ),
                chunk,
            )
            # Bold then italic
            chunk = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", chunk)
            chunk = re.sub(r"\*(.+?)\*", r"<i>\1</i>", chunk)
            # Newlines
            chunk = chunk.replace("\n", "<br>")
            return chunk

        # Preserve that our last_html already ends with a <br> if needed.
        # Process the delta — if the delta crosses a markdown boundary
        # (e.g. a closing `*` is in the new chunk) our simple approach
        # is correct for *opening* markers but may fail for *closing* ones
        # that started before the boundary.  For agent streaming this is
        # a rare edge case; the user still sees valid text with potentially
        # one un-styled character until the next token completes it.
        processed = _inline_transforms(html_delta)
        self._last_html += processed
        self.text_label.setText(self._last_html + cursor)
        self.text_label.updateGeometry()
        self.adjustSize()
        self.updateGeometry()

    def finalize_text(self, text: str):
        """Stream end — full markdown render, cursor removed, geometry updated."""
        self.text = text
        # Reset streaming delta cache so a later set_streaming_text starts fresh.
        self._last_text = ""
        self._last_html = ""
        if not self.is_user and not self.is_tool and not self.is_error:
            self.text_label.setText(_md_to_html(text))
        else:
            self.text_label.setText(html.escape(text))
        self.text_label.updateGeometry()
        self.adjustSize()
        self.updateGeometry()


class MessageContainer(QWidget):
    """Container for a bubble with optional sender label.

    - User messages: right-aligned pill (max 280px).
    - Agent / tool / error messages: left-aligned, full width.
    """

    def __init__(
        self,
        text: str,
        sender_name: str = "",
        is_user: bool = False,
        is_error: bool = False,
        is_tool: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 0, 16, 0)
        outer.setSpacing(3)

        if sender_name and not is_tool:
            name_label = QLabel(sender_name)
            name_label.setStyleSheet(
                f"color: {_TEXT_3}; font-size: 10px; background: transparent; border: none;"
            )
            name_label.setTextFormat(Qt.PlainText)
            name_label.setAlignment(Qt.AlignRight if is_user else Qt.AlignLeft)
            outer.addWidget(name_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.bubble = MessageBubble(text, sender_name, is_user, is_error, is_tool)

        if is_user:
            self.bubble.setMaximumWidth(280)
            self.bubble.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
            row.addStretch(1)
            row.addWidget(self.bubble)
        else:
            self.bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            row.addWidget(self.bubble)

        outer.addLayout(row)

    def set_streaming_text(self, text: str):
        self.bubble.set_streaming_text(text)

    def finalize_text(self, text: str):
        self.bubble.finalize_text(text)
