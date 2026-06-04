"""Code block widget — Carbon.sh inspired, minimal, no AI SLOP.

Pure dark surface, generous padding, SF Mono typography, subtle header.
"""

import html

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QMenu,
    QVBoxLayout,
    QWidget,
)

# Carbon.sh-inspired palette
_BG        = "#111111"
_HEADER_BG = "#141414"
_BORDER    = "#1a1a1a"
_TEXT      = "#cccccc"
_TEXT_DIM  = "#555555"

_MENU_STYLE = """
    QMenu {
        background-color: #202020;
        color: #cccccc;
        border: 1px solid #2b2b2b;
        border-radius: 6px;
        padding: 5px;
        font-family: 'SF Mono', 'JetBrains Mono', monospace;
        font-size: 11px;
    }
    QMenu::item {
        background-color: transparent;
        color: #cccccc;
        padding: 6px 22px 6px 10px;
        border-radius: 4px;
    }
    QMenu::item:selected {
        background-color: #232323;
        color: #e8e8e8;
    }
"""

# Syntax colors — GitHub Dark / Carbon muted
_CLR_KEYWORD = "#ff7b72"
_CLR_QGIS    = "#d2a8ff"
_CLR_STRING  = "#a5d6ff"
_CLR_NUMBER  = "#79c0ff"
_CLR_COMMENT = "#8b949e"
_CLR_FUNC    = "#d2a8ff"

PYTHON_KEYWORDS = {
    "import", "from", "as", "def", "class", "return", "if", "elif", "else",
    "for", "while", "try", "except", "finally", "with", "in", "is", "not",
    "and", "or", "True", "False", "None", "print", "yield", "raise",
}

QGIS_KEYWORDS = {
    "Qgs", "iface", "QgsProject", "QgsVectorLayer", "QgsFeature", "Qgis",
    "processing", "QgsMapLayer", "QgsApplication", "QgsGeometry",
    "QgsCoordinateReferenceSystem", "QgsField", "QgsRectangle",
}


class SimplePythonHighlighter(QSyntaxHighlighter):
    """Lightweight syntax highlighter — GitHub Dark palette."""

    def __init__(self, document):
        super().__init__(document)
        self._setup_formats()
        self._setup_rules()

    def _setup_formats(self):
        self.keyword_fmt = QTextCharFormat()
        self.keyword_fmt.setForeground(QColor(_CLR_KEYWORD))
        self.keyword_fmt.setFontWeight(QFont.DemiBold)

        self.qgis_fmt = QTextCharFormat()
        self.qgis_fmt.setForeground(QColor(_CLR_QGIS))

        self.string_fmt = QTextCharFormat()
        self.string_fmt.setForeground(QColor(_CLR_STRING))

        self.number_fmt = QTextCharFormat()
        self.number_fmt.setForeground(QColor(_CLR_NUMBER))

        self.comment_fmt = QTextCharFormat()
        self.comment_fmt.setForeground(QColor(_CLR_COMMENT))
        self.comment_fmt.setFontItalic(True)

        self.function_fmt = QTextCharFormat()
        self.function_fmt.setForeground(QColor(_CLR_FUNC))

    def _setup_rules(self):
        from qgis.PyQt.QtCore import QRegularExpression
        self.rules = []
        self.rules.append((QRegularExpression("#.*"), self.comment_fmt))
        self.rules.append((QRegularExpression('"[^"]*"'), self.string_fmt))
        self.rules.append((QRegularExpression("'[^']*'"), self.string_fmt))

    def highlightBlock(self, text):
        from qgis.PyQt.QtCore import QRegularExpression

        for kw in PYTHON_KEYWORDS:
            pattern = QRegularExpression(f"\\b{kw}\\b")
            match_iter = pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), self.keyword_fmt)

        for name in QGIS_KEYWORDS:
            pattern = QRegularExpression(f"\\b{name}[a-zA-Z]*\\b")
            match_iter = pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), self.qgis_fmt)

        for pattern, fmt in self.rules:
            match_iter = pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        number_pattern = QRegularExpression(r"\b\d+\.?\d*\b")
        match_iter = number_pattern.globalMatch(text)
        while match_iter.hasNext():
            match = match_iter.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.number_fmt)


class CodeBlockWidget(QWidget):
    """Carbon.sh-inspired code block — no icons, pure typography."""

    def __init__(self, code, language="python", parent=None):
        super().__init__(parent)
        self.code = code
        self.language = language
        self._build_ui()

    def _build_ui(self):
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Minimal header: language + copy ────────────────────────────
        header = QFrame()
        header.setStyleSheet(f"""
            QFrame {{
                background-color: {_HEADER_BG};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border: 1px solid {_BORDER};
                border-bottom: none;
            }}
        """)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(14, 8, 14, 8)
        hl.setSpacing(0)

        lang_label = QLabel(self.language.upper())
        lang_label.setStyleSheet(
            f"color: {_TEXT_DIM}; font-family: 'SF Mono', 'JetBrains Mono', monospace; "
            f"font-size: 10px; letter-spacing: 0.05em; background: transparent;"
        )
        hl.addWidget(lang_label)
        hl.addStretch(1)

        copy_btn = QPushButton("Copy")
        copy_btn.setCursor(Qt.PointingHandCursor)
        copy_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {_TEXT_DIM};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 10px;
            }}
            QPushButton:hover {{
                color: {_TEXT};
                border-color: {_TEXT_DIM};
            }}
        """)
        copy_btn.clicked.connect(self._copy_code)
        hl.addWidget(copy_btn)

        main_layout.addWidget(header)

        # ── Code body ─────────────────────────────────────────────────
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlainText(self.code)
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._show_context_menu)
        self.editor.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {_BG};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-top: none;
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                padding: 14px 16px;
                font-family: 'SF Mono', 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
                font-size: 12.5px;
                line-height: 1.55;
            }}
        """)

        # Auto-height
        fm = QFontMetrics(self.editor.font())
        lines = self.code.count("\n") + 1
        height = min(max(fm.lineSpacing() * lines + 28, 40), 420)
        self.editor.setMaximumHeight(height)
        self.editor.setMinimumHeight(40)

        self.highlighter = SimplePythonHighlighter(self.editor.document())
        main_layout.addWidget(self.editor)

    def _copy_code(self):
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.clipboard().setText(self.code)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_STYLE)
        copy_action = menu.addAction("Copy code")
        copy_action.triggered.connect(self._copy_code)
        menu.exec_(self.editor.mapToGlobal(pos))

    def set_code(self, code):
        self.code = code
        self.editor.setPlainText(code)
