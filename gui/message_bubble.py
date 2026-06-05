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
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import (
    DOCK_CANVAS as _CANVAS,
    DOCK_SURFACE as _SURFACE,
    DOCK_SURFACE_2 as _SURFACE_2,
    DOCK_BORDER as _BORDER,
    DOCK_BORDER_SOFT as _BORDER_SOFT,
    DOCK_TEXT as _TEXT,
    DOCK_TEXT_2 as _TEXT_2,
    DOCK_TEXT_3 as _TEXT_3,
    DOCK_TEXT_4 as _TEXT_4,
    DOCK_ACCENT as _ACCENT,
    DOCK_ACCENT_DIM as _ACCENT_DIM,
    DOCK_ACCENT_HOV as _ACCENT_HOV,
    DOCK_PURPLE as _PURPLE,
    DOCK_WARN as _WARN,
    DOCK_SUCCESS as _SUCCESS,
    DOCK_DANGER as _DANGER,
    DOCK_CODE_GREEN as _CODE_GREEN,
)

# ── One Dark palette (carbon.sh default theme) ─────────────────────────

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


_CODE_MENU_STYLE = f"""
    QMenu {{
        background-color: #202020;
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        padding: 5px;
        font-family: 'JetBrains Mono', 'Fira Code', monospace;
        font-size: 11px;
    }}
    QMenu::item {{
        background-color: transparent;
        color: {_TEXT};
        padding: 6px 22px 6px 10px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {_SURFACE_2};
        color: {_TEXT};
    }}
    QMenu::item:disabled {{
        color: {_TEXT_3};
        background-color: transparent;
        padding: 5px 22px 5px 10px;
    }}
    QMenu::separator {{
        height: 1px;
        background: {_BORDER};
        margin: 5px 6px;
    }}
"""


def _extract_fenced_code_blocks(text: str) -> list:
    """Return raw fenced-code block bodies from markdown text."""
    if not text:
        return []
    return [
        html.unescape(match.group(2))
        for match in re.finditer(r"```([^\n]*)\n(.*?)```", text, flags=re.DOTALL)
    ]


def _copy_to_clipboard(text: str) -> None:
    QApplication.clipboard().setText(text or "")


def _build_code_context_menu(parent, code_blocks: list, copy_message: str = ""):
    """Build an opaque context menu for copying code blocks/message text."""
    menu = QMenu(parent)
    menu.setStyleSheet(_CODE_MENU_STYLE)
    title = menu.addAction("Code block" if code_blocks else "Message")
    title.setEnabled(False)

    for index, code in enumerate(code_blocks):
        label = "Copy code" if len(code_blocks) == 1 else f"Copy code block {index + 1}"
        action = menu.addAction(label)
        action.triggered.connect(lambda _checked=False, c=code: _copy_to_clipboard(c))

    if copy_message and not code_blocks:
        action = menu.addAction("Copy message")
        action.triggered.connect(lambda _checked=False, t=copy_message: _copy_to_clipboard(t))
    return menu


def _show_code_context_menu(parent, label, pos, text: str):
    blocks = _extract_fenced_code_blocks(text)
    if not blocks and not text:
        return
    menu = _build_code_context_menu(parent, blocks, copy_message=text)
    menu.exec_(label.mapToGlobal(pos))


def _table_cells(line: str) -> list:
    """Parse one escaped markdown table row into plain text cells."""
    cells = [c.strip() for c in line.split("|")]
    cells = [c for j, c in enumerate(cells) if c or (0 < j < len(cells) - 1)]

    def clean_cell(cell: str) -> str:
        cell = html.unescape(cell)
        # Tables render as preformatted text, so keep common inline markdown
        # readable instead of leaking literal formatting markers.
        cell = re.sub(r"`([^`]+)`", r"\1", cell)
        cell = re.sub(r"\*\*(.+?)\*\*", r"\1", cell)
        cell = re.sub(r"\*(.+?)\*", r"\1", cell)
        return cell

    return [clean_cell(c) for c in cells]


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
    """Convert a markdown table to a bounded overflow-safe monospace block.

    QLabel's rich-text table layout can let wide cells paint over later columns.
    A preformatted block keeps each row as one clipped/overflowing line instead
    of asking Qt to solve column layout.
    """
    raw = match.group(0)
    lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
    # Skip separator lines: rows whose non-pipe content is only -, :, space
    data_lines = [ln for ln in lines if not re.match(r'^[\s|:\-]+$', ln)]
    if not data_lines:
        return raw

    rows = [_table_cells(ln) for ln in data_lines]
    rows = [row for row in rows if row]
    if not rows:
        return raw

    cols = max(len(row) for row in rows)
    widths = []
    for i in range(cols):
        widths.append(max(len(row[i]) if i < len(row) else 0 for row in rows))

    def fmt_row(row: list) -> str:
        padded = []
        for i in range(cols):
            value = row[i] if i < len(row) else ""
            padded.append(value.ljust(widths[i]))
        return "  ".join(padded).rstrip()

    rendered_lines = [fmt_row(rows[0])]
    rendered_lines.append("  ".join("-" * max(3, w) for w in widths).rstrip())
    rendered_lines.extend(fmt_row(row) for row in rows[1:])
    table_text = "\n".join(rendered_lines)

    block_style = (
        f"background:{_SURFACE_2}; border:1px solid {_BORDER}; border-radius:6px; "
        f"margin:8px 0 12px 0; padding:8px 10px; overflow-x:auto; overflow-y:hidden;"
    )
    pre_style = (
        f"margin:0; padding:0; color:{_TEXT_2}; background:transparent; "
        f"font-size:12px; line-height:1.45; "
        f"font-family:'JetBrains Mono','Fira Code',monospace; white-space:pre;"
    )
    spacer = '<br><span style="font-size:4px; line-height:4px;">&nbsp;</span><br>'
    return (
        f'<div style="{block_style}">'
        f'<pre style="{pre_style}">{html.escape(table_text)}</pre>'
        f'</div>{spacer}'
    )


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

    table_blocks = []

    def _save_table_block(m):
        rendered = _render_md_table(m)
        placeholder = f"\x00TABLE{len(table_blocks)}\x00"
        table_blocks.append(rendered)
        return placeholder

    # Markdown tables must be protected before inline-code conversion. Otherwise
    # a table cell containing `code` becomes literal <code style=...> text inside
    # the preformatted table block.
    safe = re.sub(r"(?m)(?:^\|[^\n]*\n){2,}(?:^\|[^\n]*)?", _save_table_block, safe)

    # Headings — inline bold, not block dividers; keeps prose flow natural
    safe = re.sub(
        r"(?m)^### (.+)$",
        lambda m: f'<b style="color:{_TEXT_2}; font-size:12px;">{m.group(1)}</b>',
        safe,
    )
    safe = re.sub(
        r"(?m)^## (.+)$",
        lambda m: f'<b style="color:{_TEXT}; font-size:12px;">{m.group(1)}</b>',
        safe,
    )
    safe = re.sub(
        r"(?m)^# (.+)$",
        lambda m: f'<b style="color:{_TEXT}; font-size:13px;">{m.group(1)}</b>',
        safe,
    )

    # Bullet list items — gentle indent, no heavy dash
    safe = re.sub(
        r"(?m)^- (.+)$",
        lambda m: (
            f'<div style="padding-left:10px; color:{_TEXT}; line-height:1.5; margin:0;">'
            f'<span style="color:{_TEXT_3}; margin-right:5px;">·</span>{m.group(1)}</div>'
        ),
        safe,
    )

    # Numbered list items
    safe = re.sub(
        r"(?m)^(\d+)\. (.+)$",
        lambda m: (
            f'<div style="padding-left:10px; color:{_TEXT}; line-height:1.5; margin:0;">'
            f'<span style="color:{_TEXT_3}; margin-right:5px;">{m.group(1)}.</span>{m.group(2)}</div>'
        ),
        safe,
    )

    safe = re.sub(
        r"`([^`]+)`",
        lambda m: (
            f'<code style="background:{_SURFACE_2}; color:{_TEXT}; '
            f'border:1px solid {_BORDER}; '
            f'border-radius:3px; padding:1px 4px; font-family:monospace; '
            f'font-size:11.5px;">{m.group(1)}</code>'
        ),
        safe,
    )

    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"\*(.+?)\*", r"<i>\1</i>", safe)

    # Strip newlines adjacent to block <div>s before converting \n → <br>
    # Otherwise </div>\n becomes </div><br> — a blank line after every bullet.
    safe = re.sub(r'</div>\n', '</div>', safe)
    safe = re.sub(r'\n<div', '<div', safe)
    # Collapse 3+ consecutive <br> down to 2 (paragraph break, not page break)
    safe = re.sub(r'(<br\s*/?>\s*){3,}', '<br><br>', safe)
    safe = safe.replace("\n", "<br>")

    for i, block in enumerate(code_blocks):
        safe = safe.replace(f"\x00CODE{i}\x00", block)
    for i, block in enumerate(table_blocks):
        safe = safe.replace(f"\x00TABLE{i}\x00", block)

    # Wrap in prose container for consistent line-height and font
    return (
        f'<div style="line-height:1.5; font-size:12px; color:{_TEXT};'
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
            f'<code style="background:{_SURFACE_2}; color:{_TEXT}; '
            f'border:1px solid {_BORDER}; '
            f'border-radius:3px; padding:1px 4px; font-family:monospace; '
            f'font-size:11.5px;">{m.group(1)}</code>'
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
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        if self.is_error:
            bg_color      = _SURFACE
            text_color    = _DANGER
            border_color  = _DANGER
            border_radius = "4px"
        elif self.is_tool:
            bg_color      = _SURFACE
            text_color    = _TEXT_2
            border_color  = _BORDER_SOFT
            border_radius = "4px"
        elif self.is_user:
            bg_color      = _SURFACE_2
            text_color    = _TEXT
            border_color  = _BORDER
            border_radius = "10px"
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
        self.text_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.text_label.setTextFormat(Qt.TextFormat.RichText)
        self.text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.text_label.setOpenExternalLinks(True)
        self.text_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_label.customContextMenuRequested.connect(self._show_context_menu)

        font = QFont("JetBrains Mono", 12)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text_label.setFont(font)
        self.text_label.setStyleSheet(f"""
            color: {text_color};
            background: transparent;
            border: none;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
        """)

        layout.addWidget(self.text_label)

    def _show_context_menu(self, pos):
        text = self.text or self._last_text or ""
        _show_code_context_menu(self, self.text_label, pos, text)

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
                    f'<div style="padding-left:10px; color:{_TEXT}; '
                    f'font-size:12px; line-height:1.5; margin:0;">'
                    f'<span style="color:{_TEXT_3};margin-right:5px;">·</span>{m.group(1)}</div>'
                ),
                chunk,
            )
            # Inline code
            chunk = re.sub(
                r"`([^`]+)`",
                lambda m: (
                    f'<code style="background:{_SURFACE_2}; color:{_TEXT}; '
                    f'border:1px solid {_BORDER}; '
                    f'border-radius:3px; padding:1px 4px; font-family:monospace; '
                    f'font-size:11.5px;">{m.group(1)}</code>'
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
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 0, 12, 0)
        outer.setSpacing(2)

        if sender_name and not is_tool:
            name_label = QLabel(sender_name)
            name_label.setStyleSheet(
                f"color: {_TEXT_3}; font-size: 10px; background: transparent; border: none;"
            )
            name_label.setTextFormat(Qt.TextFormat.PlainText)
            name_label.setAlignment(Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft)
            outer.addWidget(name_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.bubble = MessageBubble(text, sender_name, is_user, is_error, is_tool)

        if is_user:
            self.bubble.setMaximumWidth(280)
            self.bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Minimum)
            row.addStretch(1)
            row.addWidget(self.bubble)
        else:
            self.bubble.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            row.addWidget(self.bubble)

        outer.addLayout(row)

    def set_streaming_text(self, text: str):
        self.bubble.set_streaming_text(text)

    def finalize_text(self, text: str):
        self.bubble.finalize_text(text)
