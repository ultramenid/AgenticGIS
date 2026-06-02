"""Code block widget with syntax highlighting for PyQGIS code.

Displays code in a monospaced font with simple keyword highlighting
and a copy button. Uses only Qt — no external syntax highlighting libs.
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
    QVBoxLayout,
    QWidget,
)


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
    """Lightweight syntax highlighter for Python/PyQGIS code."""

    def __init__(self, document):
        super().__init__(document)
        self._setup_formats()
        self._setup_rules()

    def _setup_formats(self):
        # Keyword format
        self.keyword_fmt = QTextCharFormat()
        self.keyword_fmt.setForeground(QColor("#d73a49"))
        self.keyword_fmt.setFontWeight(QFont.Bold)

        # QGIS/PyQGIS format
        self.qgis_fmt = QTextCharFormat()
        self.qgis_fmt.setForeground(QColor("#6f42c1"))
        self.qgis_fmt.setFontWeight(QFont.Bold)

        # String format
        self.string_fmt = QTextCharFormat()
        self.string_fmt.setForeground(QColor("#032f62"))

        # Number format
        self.number_fmt = QTextCharFormat()
        self.number_fmt.setForeground(QColor("#005cc5"))

        # Comment format
        self.comment_fmt = QTextCharFormat()
        self.comment_fmt.setForeground(QColor("#6a737d"))
        self.comment_fmt.setFontStyle(QFont.StyleItalic)

        # Function format
        self.function_fmt = QTextCharFormat()
        self.function_fmt.setForeground(QColor("#6f42c1"))

    def _setup_rules(self):
        from qgis.PyQt.QtCore import QRegularExpression
        
        self.rules = []
        # Single-line comments
        self.rules.append((QRegularExpression("#.*"), self.comment_fmt))
        # Strings (single and double)
        self.rules.append((QRegularExpression("\"[^\"]*\""), self.string_fmt))
        self.rules.append((QRegularExpression("'[^']*'"), self.string_fmt))

    def highlightBlock(self, text):
        from qgis.PyQt.QtCore import QRegularExpression
        
        # Highlight keywords
        for kw in PYTHON_KEYWORDS:
            pattern = QRegularExpression(f"\\b{kw}\\b")
            match_iter = pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), self.keyword_fmt)

        # Highlight QGIS names
        for name in QGIS_KEYWORDS:
            pattern = QRegularExpression(f"\\b{name}[a-zA-Z]*\\b")
            match_iter = pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), self.qgis_fmt)

        # Apply regex rules
        for pattern, fmt in self.rules:
            match_iter = pattern.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # Numbers
        number_pattern = QRegularExpression(r"\b\d+\.?\d*\b")
        match_iter = number_pattern.globalMatch(text)
        while match_iter.hasNext():
            match = match_iter.next()
            self.setFormat(match.capturedStart(), match.capturedLength(), self.number_fmt)


class CodeBlockWidget(QWidget):
    """A styled code block with syntax highlighting and copy button."""

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

        # Header bar with language label and copy button
        header = QFrame()
        header.setStyleSheet("""
            QFrame {
                background-color: #2d2d2d;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 4)
        
        lang_label = QLabel(self.language.upper())
        lang_label.setStyleSheet("color: #e0e0e0; font: 10px; font-weight: bold;")
        header_layout.addWidget(lang_label)
        header_layout.addStretch(1)
        
        copy_btn = QPushButton("Copy")
        copy_btn.setStyleSheet("""
            QPushButton {
                background-color: #424242;
                color: #e0e0e0;
                border: none;
                border-radius: 3px;
                padding: 2px 8px;
                font: 9px;
            }
            QPushButton:hover {
                background-color: #505050;
            }
        """)
        copy_btn.clicked.connect(self._copy_code)
        header_layout.addWidget(copy_btn)
        
        main_layout.addWidget(header)

        # Code editor with syntax highlighting
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlainText(self.code)
        self.editor.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: none;
                border-bottom-left-radius: 6px;
                border-bottom-right-radius: 6px;
                padding: 8px;
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                font-size: 11px;
            }
        """)
        
        # Calculate height based on lines
        fm = QFontMetrics(self.editor.font())
        lines = self.code.count("\n") + 1
        height = min(max(fm.lineSpacing() * lines + 16, 40), 300)
        self.editor.setMaximumHeight(height)
        self.editor.setMinimumHeight(40)

        # Setup highlighter
        self.highlighter = SimplePythonHighlighter(self.editor.document())
        
        main_layout.addWidget(self.editor)

    def _copy_code(self):
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.clipboard().setText(self.code)
        
    def set_code(self, code):
        self.code = code
        self.editor.setPlainText(code)
