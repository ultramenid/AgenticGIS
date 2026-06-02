"""Chart widget — dark palette, Qt QPainter only.

Uses only Qt QPainter — no matplotlib, no external dependencies.
Renders charts embedded directly in the chat transcript.
"""

from qgis.PyQt.QtCore import Qt, QPoint, QSize
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QBrush, QPolygon
from qgis.PyQt.QtWidgets import QFrame, QSizePolicy

_SURFACE  = "#131316"
_INPUT_BG = "#1c1c20"
_BORDER   = "#27272a"
_TEXT     = "#fafafa"
_TEXT_2   = "#a1a1aa"
_TEXT_3   = "#71717a"

COLORS = [
    "#2196f3", "#22c55e", "#ff9800", "#ef4444", "#9c27b0",
    "#03a9f4", "#f59e0b", "#8bc34a", "#e91e63", "#673ab7",
]


class ChartWidget(QFrame):
    """Renders bar, line, or pie/donut charts from chart data."""

    def __init__(self, chart_data, parent=None):
        super().__init__(parent)
        self.chart_data = chart_data
        self.chart_type = chart_data.get("chart_type", "bar")
        self.title = chart_data.get("title", "")
        self.data = chart_data.get("data", [])
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"""
            ChartWidget {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 10px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def minimumSizeHint(self):
        return QSize(100, 180)

    def sizeHint(self):
        return QSize(400, 260)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        painter.fillRect(self.rect(), QColor(_INPUT_BG))

        pad = 16
        rect = self.rect().adjusted(pad, pad, -pad, -pad)

        title_h = 0
        if self.title:
            font = QFont("Inter", 10, QFont.Bold)
            painter.setFont(font)
            painter.setPen(QColor(_TEXT))
            painter.drawText(
                rect.left(), rect.top(), rect.width(), 20,
                Qt.AlignLeft | Qt.AlignVCenter, self.title
            )
            title_h = 24

        chart_rect = rect.adjusted(0, title_h, 0, 0)

        if not self.data:
            self._draw_empty(painter, chart_rect)
            painter.end()
            return

        if self.chart_type == "bar":
            self._draw_bar(painter, chart_rect)
        elif self.chart_type == "line":
            self._draw_line(painter, chart_rect)
        elif self.chart_type == "pie":
            self._draw_pie(painter, chart_rect)

        painter.end()

    def _draw_empty(self, painter, rect):
        font = QFont("Inter", 9)
        painter.setFont(font)
        painter.setPen(QColor(_TEXT_3))
        painter.drawText(rect, Qt.AlignCenter, "No data")

    def _draw_bar(self, painter, rect):
        bars = self.data[:10]
        max_val = max((item["value"] for item in bars), default=1) or 1

        bottom = rect.bottom() - 22
        chart_h = bottom - rect.top() - 16
        n = len(bars)
        slot_w = rect.width() / n
        bar_w = max(4, int(slot_w * 0.62))

        # Grid lines at 50% and 100%
        grid_pen = QPen(QColor(_BORDER), 1, Qt.DashLine)
        painter.setPen(grid_pen)
        for frac in (0.5, 1.0):
            gy = int(bottom - frac * chart_h)
            painter.drawLine(rect.left(), gy, rect.right(), gy)

        painter.setPen(QPen(QColor(_BORDER), 1))
        painter.drawLine(rect.left(), bottom, rect.right(), bottom)

        font = QFont("Inter", 8)
        painter.setFont(font)

        for i, item in enumerate(bars):
            x = int(rect.left() + i * slot_w + (slot_w - bar_w) / 2)
            bar_h = int((item["value"] / max_val) * chart_h)
            y = bottom - bar_h

            color = QColor(COLORS[i % len(COLORS)])
            painter.fillRect(x, y, bar_w, bar_h, color)

            val_str = str(item["value"])
            painter.setPen(QColor(_TEXT_2))
            painter.drawText(x, y - 2, bar_w, 12, Qt.AlignCenter, val_str)

            lbl = str(item.get("label", ""))
            if len(lbl) > 10:
                lbl = lbl[:9] + ".."
            painter.setPen(QColor(_TEXT_3))
            painter.drawText(x, bottom + 4, bar_w, 16, Qt.AlignCenter, lbl)

    def _draw_line(self, painter, rect):
        if len(self.data) < 2:
            return
        pts = self.data[:20]
        max_val = max((p["value"] for p in pts), default=1) or 1

        bottom = rect.bottom() - 4
        chart_h = rect.height() - 8

        painter.setPen(QPen(QColor(_BORDER), 1))
        painter.drawLine(rect.left(), bottom, rect.right(), bottom)
        painter.drawLine(rect.left(), rect.top(), rect.left(), bottom)

        x_step = rect.width() / max(len(pts) - 1, 1)
        line_color = QColor(COLORS[0])

        coords = []
        for i, item in enumerate(pts):
            x = int(rect.left() + i * x_step)
            y = int(bottom - (item["value"] / max_val) * chart_h)
            coords.append((x, y))

        # Fill area under line
        if len(coords) >= 2:
            fill_color = QColor(line_color)
            fill_color.setAlpha(40)
            poly_pts = [QPoint(coords[0][0], bottom)]
            for x, y in coords:
                poly_pts.append(QPoint(x, y))
            poly_pts.append(QPoint(coords[-1][0], bottom))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(fill_color))
            painter.drawPolygon(QPolygon(poly_pts))

        pen = QPen(line_color, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for i in range(1, len(coords)):
            painter.drawLine(
                coords[i - 1][0], coords[i - 1][1],
                coords[i][0], coords[i][1]
            )

        painter.setBrush(QBrush(line_color))
        for x, y in coords:
            painter.drawEllipse(x - 3, y - 3, 6, 6)

        # Last value label at rightmost point
        if coords:
            last_x, last_y = coords[-1]
            last_val = str(pts[-1]["value"])
            font = QFont("Inter", 8)
            painter.setFont(font)
            painter.setPen(QColor(_TEXT_2))
            painter.drawText(last_x - 20, last_y - 14, 40, 12, Qt.AlignCenter, last_val)

    def _draw_pie(self, painter, rect):
        items = self.data[:7]
        total = sum(item["value"] for item in items) or 1

        legend_w = 100
        pie_rect = rect.adjusted(0, 0, -legend_w - 8, 0)

        cx = pie_rect.left() + pie_rect.width() // 2
        cy = pie_rect.top() + pie_rect.height() // 2
        r = min(pie_rect.width(), pie_rect.height()) // 2 - 4
        hole_r = int(r * 0.4)

        start = 0
        for i, item in enumerate(items):
            span = int((item["value"] / total) * 360 * 16)
            color = QColor(COLORS[i % len(COLORS)])
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(_INPUT_BG), 2))
            painter.drawPie(cx - r, cy - r, r * 2, r * 2, start, span)
            start += span

        # Donut hole
        painter.setBrush(QBrush(QColor(_INPUT_BG)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(cx - hole_r, cy - hole_r, hole_r * 2, hole_r * 2)

        # Center label: total count or single label
        if len(items) > 1:
            center_text = str(len(items)) + " cat"
        else:
            center_text = str(items[0].get("label", "")) if items else ""
        if center_text:
            font = QFont("Inter", 8)
            painter.setFont(font)
            painter.setPen(QColor(_TEXT_2))
            painter.drawText(
                cx - hole_r, cy - hole_r, hole_r * 2, hole_r * 2,
                Qt.AlignCenter, center_text
            )

        font = QFont("Inter", 8)
        painter.setFont(font)
        lx = rect.right() - legend_w + 4
        ly = rect.top() + 8
        for i, item in enumerate(items):
            if i >= 6:
                break
            color = QColor(COLORS[i % len(COLORS)])
            painter.fillRect(lx, ly + i * 18, 10, 10, color)
            painter.setPen(QColor(_TEXT_2))
            lbl = str(item.get("label", ""))[:12]
            painter.drawText(lx + 14, ly + i * 18 + 10, lbl)
