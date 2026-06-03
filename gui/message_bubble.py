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

# ── Neural Terminal Palette ────────────────────────────────
_CANVAS      = "#060810"   # deep navy-black
_SURFACE     = "#0a0d14"   # card surface
_SURFACE_2   = "#0d1018"   # slightly elevated
_BORDER      = "#1a1f2e"   # cool dark border
_BORDER_SOFT = "#131722"   # very subtle
_TEXT        = "#cdd6e0"   # cool light
_TEXT_2      = "#7a8899"   # mid cool gray
_TEXT_3      = "#3d4a5c"   # dim
_ACCENT      = "#00d4b8"   # electric teal — PRIMARY
_ACCENT_DIM  = "#00a896"   # teal dimmed
_ACCENT_HOV  = "#00b8a0"   # teal hover
_PURPLE      = "#9d4edd"   # thinking purple
_WARN        = "#f59e0b"   # amber — tools running
_SUCCESS     = "#10b981"   # emerald — tools done
_DANGER      = "#ef4444"   # red — error

# Inline-code token color (kept for backtick spans)
_CODE_GREEN  = "#7ee787"

# ── One Dark palette (carbon.sh default theme) ─────────────────────────
_SYN_BG     = "#282c34"
_SYN_CHROME = "#21252b"
_SYN_BORDER = "#3e4451"
_SYN_TEXT   = "#abb2bf"
_SYN_CMT    = "#5c6370"   # comments — italic gray
_SYN_STR    = "#98c379"   # strings  — green
_SYN_KW     = "#c678dd"   # keywords — purple
_SYN_NUM    = "#d19a66"   # numbers  — orange
_SYN_FN     = "#61afef"   # functions/builtins — blue
_SYN_CONST  = "#56b6c2"   # true/false/None — cyan

# Keyword sets for syntax highlighting
_PY_KW = frozenset({
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
    'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
    'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is',
    'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return', 'self',
    'try', 'while', 'with', 'yield',
})
_PY_BUILTIN = frozenset({
    'bool', 'bytes', 'dict', 'enumerate', 'filter', 'float', 'frozenset',
    'getattr', 'hasattr', 'input', 'int', 'isinstance', 'issubclass', 'len',
    'list', 'map', 'max', 'min', 'next', 'object', 'open', 'print', 'range',
    'reversed', 'set', 'setattr', 'sorted', 'str', 'sum', 'super', 'tuple',
    'type', 'vars', 'zip',
})
_JS_KW = frozenset({
    'async', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue',
    'debugger', 'default', 'delete', 'do', 'else', 'export', 'extends',
    'false', 'finally', 'for', 'from', 'function', 'if', 'import', 'in',
    'instanceof', 'let', 'new', 'null', 'of', 'return', 'static', 'super',
    'switch', 'this', 'throw', 'true', 'try', 'typeof', 'undefined', 'var',
    'void', 'while', 'yield',
})
_GENERIC_KW = frozenset({
    'if', 'else', 'for', 'while', 'return', 'class', 'function', 'import',
    'export', 'const', 'let', 'var', 'def', 'async', 'await', 'true',
    'false', 'null', 'True', 'False', 'None', 'in', 'is', 'not', 'and',
    'or', 'new', 'this', 'try', 'catch', 'finally', 'throw', 'yield',
    'from', 'pass', 'break', 'continue', 'static', 'public', 'private',
    'protected', 'extends', 'void', 'type', 'interface', 'package',
})


def _highlight_code(body: str, lang: str) -> str:
    """Apply One Dark syntax coloring to an html-escaped code body.

    Unescape → match raw text → re-escape non-slot segments → restore slots.
    This avoids &quot; / &lt; confusion when matching string literals.
    """
    import html as _h

    raw = _h.unescape(body)
    slots: list = []

    def save(raw_text: str, color: str, italic: bool = False) -> str:
        i = len(slots)
        st = f"color:{color};"
        if italic:
            st += "font-style:italic;"
        slots.append(f'<span style="{st}">{_h.escape(raw_text)}</span>')
        # Wrap digits in letters so \b word-boundary can't match inside the marker
        return f"\x01S{i:06d}E\x01"

    # 1. Triple-quoted strings (Python)
    raw = re.sub(
        r'(""".*?"""|\'\'\'.*?\'\'\')',
        lambda m: save(m.group(0), _SYN_STR),
        raw, flags=re.DOTALL,
    )

    # 2. Comments — language-aware
    _HASH_LANGS  = {'python', 'py', 'bash', 'sh', 'shell', 'r', 'yaml', 'toml', 'ruby'}
    _SLASH_LANGS = {'javascript', 'js', 'typescript', 'ts', 'java', 'c', 'cpp',
                    'go', 'rust', 'kotlin', 'swift', 'css', 'scss', 'sql'}
    if lang in _HASH_LANGS:
        raw = re.sub(r'(#[^\n]*)', lambda m: save(m.group(0), _SYN_CMT, italic=True), raw)
    elif lang in _SLASH_LANGS:
        raw = re.sub(r'(//[^\n]*)', lambda m: save(m.group(0), _SYN_CMT, italic=True), raw)
        raw = re.sub(r'(/\*.*?\*/)', lambda m: save(m.group(0), _SYN_CMT, italic=True), raw, flags=re.DOTALL)
    else:
        raw = re.sub(r'(#[^\n]*|//[^\n]*)', lambda m: save(m.group(0), _SYN_CMT, italic=True), raw)

    # 3. Double-quoted strings
    raw = re.sub(r'("(?:[^"\\\\]|\\\\.)*")', lambda m: save(m.group(0), _SYN_STR), raw)

    # 4. Single-quoted strings (min 2 chars, avoids contractions)
    raw = re.sub(r"('(?:[^'\\\\\n]|\\\\.){1,300}')", lambda m: save(m.group(0), _SYN_STR), raw)

    # 5. Numbers (hex, float, int, scientific)
    raw = re.sub(
        r'\b(0x[0-9a-fA-F]+|\d+\.?\d*(?:[eE][+-]?\d+)?)\b',
        lambda m: save(m.group(0), _SYN_NUM),
        raw,
    )

    # 6. Keyword / builtin sets
    if lang in ('python', 'py'):
        kw, bi = _PY_KW, _PY_BUILTIN
    elif lang in ('javascript', 'js', 'typescript', 'ts'):
        kw, bi = _JS_KW, frozenset()
    else:
        kw, bi = _GENERIC_KW, frozenset()

    # 6a. Function calls — color name blue unless it's a keyword
    def _fn_call(m):
        w = m.group(1)
        if w in kw:
            return m.group(0)
        return save(w, _SYN_FN) + '('

    raw = re.sub(r'\b([A-Za-z_]\w*)\s*\(', _fn_call, raw)

    # 6b. Keywords and builtins (remaining words)
    def _kw_sub(m):
        w = m.group(0)
        if w in kw:
            return save(w, _SYN_KW)
        if w in bi:
            return save(w, _SYN_FN)
        return w

    raw = re.sub(r'\b[A-Za-z_]\w*\b', _kw_sub, raw)

    # 7. Re-escape plain text, preserve slot markers intact
    _SLOT_RE = re.compile(r'(\x01S\d{6}E\x01)')
    parts = _SLOT_RE.split(raw)
    out = "".join(
        p if _SLOT_RE.fullmatch(p) else _h.escape(p, quote=False)
        for p in parts
    )

    # 8. Restore slots
    for i, s in enumerate(slots):
        out = out.replace(f"\x01S{i:06d}E\x01", s)

    return out


def _render_md_table(match) -> str:
    """Convert a matched markdown table block to a styled HTML table."""
    raw = match.group(0)
    lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
    # Skip separator lines: rows whose non-pipe content is only -, :, space
    data_lines = [ln for ln in lines if not re.match(r'^[\s|:\-]+$', ln)]
    if not data_lines:
        return raw

    th_style = (
        f"padding:3px 8px; text-align:left; color:{_TEXT}; font-weight:500; "
        f"border-bottom:1px solid {_BORDER}; background:{_SURFACE}; white-space:nowrap;"
    )
    td_style = (
        f"padding:2px 8px; text-align:left; color:{_TEXT_2}; "
        f"border-bottom:1px solid {_BORDER_SOFT};"
    )
    table_style = (
        f"border-collapse:collapse; width:100%; margin:2px 0; "
        f"font-size:12px; font-family:'JetBrains Mono','Fira Code',monospace; "
        f"background:{_SURFACE_2}; border:1px solid {_BORDER}; border-radius:6px;"
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
        lang = (m.group(1) or "").strip().lower()

        highlighted = _highlight_code(body, lang)

        # Window chrome — traffic lights left, language badge right
        dots = (
            '<span style="color:#ff5f56;font-size:10px;">&#9679;</span>'
            '<span style="color:#ffbd2e;font-size:10px;margin-left:5px;">&#9679;</span>'
            '<span style="color:#27c93f;font-size:10px;margin-left:5px;">&#9679;</span>'
        )
        lang_badge = (
            f'<span style="color:{_SYN_CMT};font-family:\'JetBrains Mono\',monospace;'
            f'font-size:9px;letter-spacing:0.08em;">{lang.upper()}</span>'
            if lang else ""
        )
        chrome = (
            f'<div style="background:{_SYN_CHROME};padding:9px 14px 8px 14px;'
            f'border-bottom:1px solid {_SYN_BORDER};border-radius:14px 14px 0 0;">'
            f'<table width="100%" cellspacing="0" cellpadding="0" border="0"><tr>'
            f'<td style="vertical-align:middle;">{dots}</td>'
            f'<td align="right" style="vertical-align:middle;">{lang_badge}</td>'
            f'</tr></table>'
            f'</div>'
        )

        rendered = (
            f'<div style="background:{_SYN_BG};border:1px solid {_SYN_BORDER};'
            f'border-radius:14px;margin:8px 0;">'
            f'{chrome}'
            f'<div style="padding:16px 18px;border-radius:0 0 14px 14px;">'
            f'<pre style="margin:0;padding:0;background:transparent;border:none;'
            f'font-family:\'JetBrains Mono\',\'Fira Code\',monospace;'
            f'font-size:12.5px;line-height:1.6;color:{_SYN_TEXT};'
            f'white-space:pre-wrap;">{highlighted}</pre>'
            f'</div>'
            f'</div>'
        )
        placeholder = f"\x00CODE{len(code_blocks)}\x00"
        code_blocks.append(rendered)
        return placeholder

    safe = re.sub(r"```([^\n]*)\n(.*?)```", _save_code_block, safe, flags=re.DOTALL)

    # Headings — inline bold, not block dividers; keeps prose flow natural
    safe = re.sub(
        r"(?m)^### (.+)$",
        lambda m: f'<b style="color:{_TEXT_2};">{m.group(1)}</b>',
        safe,
    )
    safe = re.sub(
        r"(?m)^## (.+)$",
        lambda m: f'<b style="color:{_TEXT};">{m.group(1)}</b>',
        safe,
    )
    safe = re.sub(
        r"(?m)^# (.+)$",
        lambda m: f'<b style="color:{_TEXT}; font-size:14px;">{m.group(1)}</b>',
        safe,
    )

    # Bullet list items — gentle indent, no heavy dash
    safe = re.sub(
        r"(?m)^- (.+)$",
        lambda m: (
            f'<div style="padding-left:10px; color:{_TEXT}; line-height:1.5; margin:0;">'
            f'<span style="color:#7a8899; margin-right:5px;">·</span>{m.group(1)}</div>'
        ),
        safe,
    )

    # Numbered list items
    safe = re.sub(
        r"(?m)^(\d+)\. (.+)$",
        lambda m: (
            f'<div style="padding-left:10px; color:{_TEXT}; line-height:1.5; margin:0;">'
            f'<span style="color:#7a8899; margin-right:5px;">{m.group(1)}.</span>{m.group(2)}</div>'
        ),
        safe,
    )

    safe = re.sub(
        r"`([^`]+)`",
        lambda m: (
            f'<code style="background:#0d1018; color:#00d4b8; '
            f'border:1px solid #1a1f2e; '
            f'border-radius:3px; padding:1px 4px; font-family:monospace; '
            f'font-size:12px;">{m.group(1)}</code>'
        ),
        safe,
    )

    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    # Markdown tables
    safe = re.sub(r"(?m)(?:^\|[^\n]*\n){2,}(?:^\|[^\n]*)?", _render_md_table, safe)

    # Strip newlines adjacent to block <div>s before converting \n → <br>
    # Otherwise </div>\n becomes </div><br> — a blank line after every bullet.
    safe = re.sub(r'</div>\n', '</div>', safe)
    safe = re.sub(r'\n<div', '<div', safe)
    # Collapse 3+ consecutive <br> down to 2 (paragraph break, not page break)
    safe = re.sub(r'(<br\s*/?>\s*){3,}', '<br><br>', safe)
    safe = safe.replace("\n", "<br>")

    for i, block in enumerate(code_blocks):
        safe = safe.replace(f"\x00CODE{i}\x00", block)

    # Wrap in prose container for consistent line-height and font
    return (
        f'<div style="line-height:1.5; font-size:12px; color:#cdd6e0;'
        f" font-family:'JetBrains Mono','Fira Code',monospace;\">"
        f'{safe}</div>'
    )


def _md_inline(text: str) -> str:
    """Streaming path — inline markdown only, no fenced code blocks.

    Applies bold, italic, inline code, and bullet points.
    Fenced code blocks are intentionally skipped to avoid showing a half-rendered
    fence while the closing ``` has not arrived yet during streaming.
    """
    safe = html.escape(text)

    # Bullet list items — gentle indent, small dot
    safe = re.sub(
        r"(?m)^- (.+)$",
        lambda m: (
            f'<div style="padding-left:10px; color:{_TEXT}; line-height:1.5; margin:0;">'
            f'<span style="color:{_TEXT_3}; margin-right:5px;">·</span>{m.group(1)}</div>'
        ),
        safe,
    )

    # Inline code
    safe = re.sub(
        r"`([^`]+)`",
        lambda m: (
            f'<code style="background:#0d1018; color:#00d4b8; '
            f'border:1px solid #1a1f2e; '
            f'border-radius:3px; padding:1px 4px; font-family:monospace; '
            f'font-size:12px;">{m.group(1)}</code>'
        ),
        safe,
    )

    # Bold then italic
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    safe = re.sub(r'</div>\n', '</div>', safe)
    safe = re.sub(r'\n<div', '<div', safe)
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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        if self.is_error:
            bg_color      = "#2a0f10"
            text_color    = "#fecaca"
            border_color  = "#7f1d1d"
            border_radius = "4px"
        elif self.is_tool:
            bg_color      = _SURFACE
            text_color    = _TEXT_2
            border_color  = _BORDER_SOFT
            border_radius = "4px"
        elif self.is_user:
            bg_color      = "#0d1523"
            text_color    = "#cdd6e0"
            border_color  = "#1e3a5f"
            border_radius = "12px"
        else:
            bg_color      = _SURFACE
            text_color    = _TEXT
            border_color  = _BORDER
            border_radius = "4px"

        self.setStyleSheet(f"""
            MessageBubble {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: {border_radius};
                padding: 0px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(0)

        initial_html = html.escape(self.text) if self.text else ""

        self.text_label = QLabel(initial_html)
        self.text_label.setWordWrap(True)
        self.text_label.setMinimumWidth(0)  # allow label to shrink to viewport width
        self.text_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.text_label.setTextFormat(Qt.RichText)
        self.text_label.setTextInteractionFlags(
            Qt.TextBrowserInteraction | Qt.TextSelectableByMouse
        )
        self.text_label.setOpenExternalLinks(True)

        font = QFont("JetBrains Mono", 13)
        font.setStyleHint(QFont.Monospace)
        self.text_label.setFont(font)
        self.text_label.setStyleSheet(f"""
            color: {text_color};
            background: transparent;
            border: none;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
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

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        if hasattr(self, "text_label") and self.layout():
            m = self.layout().contentsMargins()
            inner_w = width - m.left() - m.right()
            if inner_w > 0:
                lh = self.text_label.heightForWidth(inner_w)
                if lh > 0:
                    return lh + m.top() + m.bottom()
        return -1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Fix label render width so word-wrap displays correctly.
        # No updateGeometry() here — hasHeightForWidth handles layout sizing.
        if hasattr(self, "text_label") and self.layout():
            m = self.layout().contentsMargins()
            w = event.size().width() - m.left() - m.right()
            if w > 0:
                self.text_label.setFixedWidth(w)

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
        # Minimal cursor — thin vertical bar, muted
        cursor = f'<span style="color:{_TEXT_3};font-weight:300;">|</span>'

        if not delta:
            # No new text — just refresh cursor position
            self.text_label.setText(self._last_html + cursor)
            return

        # Markdown-process only the delta.  We escape it then run the same
        # inline transforms _md_inline uses so bold/italic/code/bullets work.
        html_delta = html.escape(delta)

        def _inline_transforms(chunk: str) -> str:
            # Bullet list items (line-start only) — tighter, en-dash instead of heavy bullet
            chunk = re.sub(
                r"(?m)^- (.+)$",
                lambda m: (
                    f'<div style="padding-left:12px; color:{_TEXT}; '
                    f'font-size:13px; line-height:1.35; margin:0 0 1px 0;">'
                    f'<span style="color:{_TEXT_3};margin-right:6px;">—</span>{m.group(1)}</div>'
                ),
                chunk,
            )
            # Inline code
            chunk = re.sub(
                r"`([^`]+)`",
                lambda m: (
                    f'<code style="background:#0d1018; color:#00d4b8; '
                    f'border:1px solid #1a1f2e; '
                    f'border-radius:4px; padding:1px 5px; font-family:monospace; '
                    f'font-size:12px;letter-spacing:-0.01em;">{m.group(1)}</code>'
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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 0, 12, 0)
        outer.setSpacing(2)

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
            self.bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            row.addWidget(self.bubble)

        outer.addLayout(row)

    def set_streaming_text(self, text: str):
        self.bubble.set_streaming_text(text)

    def finalize_text(self, text: str):
        self.bubble.finalize_text(text)
