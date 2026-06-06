"""Chart widget — dark palette, Qt QPainter only.

Uses only Qt QPainter — no matplotlib, no external dependencies.
Renders charts embedded directly in the chat transcript.

Interactive: hover a bar / line point / pie slice to highlight it and
read the value in a tooltip that follows the cursor. Click to pin the
tooltip in place; click again or move off the chart to unpin.
Right-click to copy the data as TSV.
"""

from qgis.PyQt.QtCore import Qt, QPoint, QSize, QRect
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QBrush, QPolygon, QGuiApplication
from qgis.PyQt.QtWidgets import QFrame, QSizePolicy

# Design tokens — darker, softer (match chat_dock.py)
_SURFACE = "#161616"
_INPUT_BG = "#1e1e1e"
_BORDER = "#2e2e2e"
_TEXT = "#ececec"
_TEXT_2 = "#a0a0a0"
_TEXT_3 = "#707070"

# Default A-to-B palette for charts. Custom chart_data["colors"] still wins.
GRADIENT_START = "#79a883"
GRADIENT_END = "#d9a35f"


def _font(size, weight=QFont.Weight.Normal):
    font = QFont()
    font.setPointSize(size)
    font.setWeight(weight)
    return font


class ChartWidget(QFrame):
    """Renders bar, line, or pie/donut charts from chart data.

    Interactive: hover an element to highlight + show a tooltip with its
    value; click to pin the tooltip; right-click to copy the data.
    """

    def __init__(self, chart_data, parent=None):
        super().__init__(parent)
        self.chart_data = chart_data
        self.chart_type = chart_data.get("chart_type", "bar")
        self.title = chart_data.get("title", "")
        self.data = chart_data.get("data", [])
        # Custom color palette. None or empty falls back to the gradient.
        # The list cycles if shorter than the data — caller can supply
        # 1-N entries and we'll repeat.
        raw_colors = chart_data.get("colors") or []
        self._custom_colors = [str(c) for c in raw_colors if isinstance(c, str) and c]
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"""
            ChartWidget {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 8px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Hit-test regions populated by _draw_*.
        self._hit_regions = []
        self._hover_index = -1
        self._pinned_index = -1
        self._cursor_pos = QPoint(0, 0)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def minimumSizeHint(self):
        return QSize(100, 180)

    def sizeHint(self):
        return QSize(400, 260)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        try:
            painter.fillRect(self.rect(), QColor(_INPUT_BG))

            pad = 16
            rect = self.rect().adjusted(pad, pad, -pad, -pad)

            title_h = 0
            if self.title:
                font = _font(10, QFont.Weight.Bold)
                painter.setFont(font)
                painter.setPen(QColor(_TEXT))
                painter.drawText(
                    rect.left(), rect.top(), rect.width(), 20,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title
                )
                title_h = 24

            chart_rect = rect.adjusted(0, title_h, 0, 0)

            # Clear and rebuild hit regions each paint
            self._hit_regions = []

            if not self.data:
                self._draw_empty(painter, chart_rect)
            elif self.chart_type == "bar":
                self._draw_bar(painter, chart_rect)
            elif self.chart_type == "line":
                self._draw_line(painter, chart_rect)
            elif self.chart_type == "pie":
                self._draw_pie(painter, chart_rect)

            # Highlight the hovered or pinned element with a thin outline
            active = self._pinned_index if self._pinned_index >= 0 else self._hover_index
            if active >= 0 and active < len(self._hit_regions):
                region = self._hit_regions[active]
                kind = region["kind"]
                painter.save()
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if kind == "bar":
                    _rect = region["rect"]
                    painter.setPen(QPen(QColor(_TEXT), 1.5))
                    painter.drawRect(_rect.adjusted(-1, -1, 1, 1))
                elif kind == "line":
                    _rect = region["rect"]
                    painter.setBrush(QBrush(QColor(_TEXT)))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(_rect)
                painter.restore()
                self._draw_inspector(painter, chart_rect, region)
        except Exception:
            pass
        finally:
            painter.end()

    def _add_hit_region(self, **region):
        self._hit_regions.append(region)

    def _color_at(self, index, total=None):
        """Return the QColor for the i-th data point.

        If a custom palette was supplied via chart_data["colors"],
        cycle through it. Otherwise interpolate across the default
        A-to-B gradient.
        """
        if self._custom_colors:
            return QColor(self._custom_colors[index % len(self._custom_colors)])
        total = max(1, int(total or len(self.data) or 1))
        if total == 1:
            ratio = 0.0
        else:
            ratio = max(0.0, min(1.0, index / float(total - 1)))
        start = QColor(GRADIENT_START)
        end = QColor(GRADIENT_END)
        r = int(round(start.red() + (end.red() - start.red()) * ratio))
        g = int(round(start.green() + (end.green() - start.green()) * ratio))
        b = int(round(start.blue() + (end.blue() - start.blue()) * ratio))
        return QColor(r, g, b)

    def _format_value(self, value):
        if isinstance(value, float):
            return f"{value:.3f}".rstrip("0").rstrip(".")
        return str(value)

    def _draw_inspector(self, painter, chart_rect, region):
        label = str(region.get("label", ""))
        value = self._format_value(region.get("value", ""))
        raw_label = str(region.get("raw_label", ""))
        if raw_label and raw_label != label:
            label = f"{label} ({raw_label})"
        pct = region.get("percent")
        if pct is not None:
            value = f"{value} ({pct:.1f}%)"

        font_label = _font(8)
        font_value = _font(9, QFont.Weight.Bold)
        fm_label = QFontMetrics(font_label)
        fm_value = QFontMetrics(font_value)
        label = fm_label.elidedText(label, Qt.TextElideMode.ElideRight, 160)
        value = fm_value.elidedText(value, Qt.TextElideMode.ElideRight, 160)

        w = max(fm_label.horizontalAdvance(label), fm_value.horizontalAdvance(value)) + 20
        h = 44
        anchor = region.get("anchor", self._cursor_pos)
        x = anchor.x() + 12
        y = anchor.y() - h - 10
        if x + w > chart_rect.right():
            x = anchor.x() - w - 12
        if x < chart_rect.left():
            x = chart_rect.left()
        if y < chart_rect.top():
            y = anchor.y() + 12
        if y + h > chart_rect.bottom():
            y = chart_rect.bottom() - h

        box = QRect(int(x), int(y), int(w), h)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#383838"), 1))
        painter.setBrush(QBrush(QColor("#202020")))
        painter.drawRoundedRect(box, 6, 6)
        painter.setFont(font_label)
        painter.setPen(QColor(_TEXT_2))
        painter.drawText(box.adjusted(10, 6, -10, -22), Qt.AlignLeft | Qt.AlignVCenter, label)
        painter.setFont(font_value)
        painter.setPen(QColor(_TEXT))
        painter.drawText(box.adjusted(10, 22, -10, -6), Qt.AlignLeft | Qt.AlignVCenter, value)
        painter.restore()

    def _draw_empty(self, painter, rect):
        font = _font(9)
        painter.setFont(font)
        painter.setPen(QColor(_TEXT_3))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No data")

    def _draw_bar(self, painter, rect):
        bars = self.data[:10]
        # Safety: filter out invalid entries
        bars = [b for b in bars if isinstance(b, dict) and "label" in b and "value" in b]
        if not bars:
            return  # Empty handled in paintEvent
        max_val = max((item["value"] for item in bars if isinstance(item.get("value"), (int, float))), default=1) or 1

        bottom = rect.bottom() - 22
        chart_h = bottom - rect.top() - 16
        n = len(bars)
        slot_w = rect.width() / n
        bar_w = max(4, int(slot_w * 0.62))

        # Grid lines at 50% and 100%
        grid_pen = QPen(QColor(_BORDER), 1, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        for frac in (0.5, 1.0):
            gy = int(bottom - frac * chart_h)
            painter.drawLine(rect.left(), gy, rect.right(), gy)

        painter.setPen(QPen(QColor(_BORDER), 1))
        painter.drawLine(rect.left(), bottom, rect.right(), bottom)

        font = _font(8)
        painter.setFont(font)

        for i, item in enumerate(bars):
            x = int(rect.left() + i * slot_w + (slot_w - bar_w) / 2)
            bar_h = int((item["value"] / max_val) * chart_h)
            y = bottom - bar_h

            color = self._color_at(i, len(bars))
            # Slightly brighter on hover/pin for a quick visual feedback
            active = self._pinned_index if self._pinned_index >= 0 else self._hover_index
            if i == active:
                color = color.lighter(140)
            painter.fillRect(x, y, bar_w, bar_h, color)

            val_str = str(item["value"])
            painter.setPen(QColor(_TEXT_2))
            painter.drawText(x, y - 2, bar_w, 12, Qt.AlignmentFlag.AlignCenter, val_str)

            lbl = QFontMetrics(font).elidedText(
                str(item.get("label", "")),
                Qt.TextElideMode.ElideRight,
                max(12, bar_w),
            )
            painter.setPen(QColor(_TEXT_3))
            painter.drawText(x, bottom + 4, bar_w, 16, Qt.AlignmentFlag.AlignCenter, lbl)

            # Hit region spans the full slot (not just the bar) so users
            # can hover the label too.
            hit = QRect(int(rect.left() + i * slot_w), rect.top(),
                        int(slot_w), rect.height())
            self._add_hit_region(
                kind="bar",
                rect=hit,
                label=str(item.get("label", "")),
                raw_label=str(item.get("raw_label", "")),
                value=item["value"],
                anchor=QPoint(x + bar_w // 2, y),
            )

    def _draw_line(self, painter, rect):
        if len(self.data) < 2:
            return
        pts = [d for d in self.data[:20] if isinstance(
            d, dict) and "value" in d and isinstance(d.get("value"), (int, float))]
        if len(pts) < 2:
            return
        max_val = max((p["value"] for p in pts), default=1) or 1

        bottom = rect.bottom() - 4
        chart_h = rect.height() - 8

        painter.setPen(QPen(QColor(_BORDER), 1))
        painter.drawLine(rect.left(), bottom, rect.right(), bottom)
        painter.drawLine(rect.left(), rect.top(), rect.left(), bottom)

        x_step = rect.width() / max(len(pts) - 1, 1)
        line_color = self._color_at(0, len(pts))

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
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill_color))
            painter.drawPolygon(QPolygon(poly_pts))

        pen = QPen(line_color, 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(1, len(coords)):
            painter.drawLine(
                coords[i - 1][0], coords[i - 1][1],
                coords[i][0], coords[i][1]
            )

        # Draw points and record hit regions. The hit region is a generous
        # 12px circle around each point so users don't have to be pixel-
        # perfect.
        active = self._pinned_index if self._pinned_index >= 0 else self._hover_index
        painter.setBrush(QBrush(line_color))
        for i, ((x, y), item) in enumerate(zip(coords, pts)):
            r = 5 if i == active else 3
            painter.drawEllipse(x - r, y - r, r * 2, r * 2)
            hit = QRect(x - 8, y - 8, 16, 16)
            self._add_hit_region(
                kind="line",
                rect=hit,
                label=str(item.get("label", f"pt {i + 1}")),
                raw_label=str(item.get("raw_label", "")),
                value=item["value"],
                anchor=QPoint(x, y),
            )

        # Last value label at rightmost point
        if coords:
            last_x, last_y = coords[-1]
            last_val = str(pts[-1]["value"])
            font = _font(8)
            painter.setFont(font)
            painter.setPen(QColor(_TEXT_2))
            painter.drawText(last_x - 20, last_y - 14, 40, 12, Qt.AlignmentFlag.AlignCenter, last_val)

    def _draw_pie(self, painter, rect):
        items = [d for d in self.data[:7] if isinstance(
            d, dict) and "value" in d and isinstance(d.get("value"), (int, float))]
        if not items:
            self._draw_empty(painter, rect)
            return
        total = sum(item["value"] for item in items) or 1

        legend_w = 100
        pie_rect = rect.adjusted(0, 0, -legend_w - 8, 0)

        cx = pie_rect.left() + pie_rect.width() // 2
        cy = pie_rect.top() + pie_rect.height() // 2
        r = min(pie_rect.width(), pie_rect.height()) // 2 - 4
        hole_r = int(r * 0.4)

        active = self._pinned_index if self._pinned_index >= 0 else self._hover_index

        from math import cos, sin, radians
        start = 0
        for i, item in enumerate(items):
            span = int((item["value"] / total) * 360 * 16)
            color = self._color_at(i, len(items))
            if i == active:
                color = color.lighter(140)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(_INPUT_BG), 2))
            painter.drawPie(cx - r, cy - r, r * 2, r * 2, start, span)

            mid_angle = (start + span / 2) / 16.0  # back to degrees
            mx = int(cx + (r * 0.7) * cos(radians(mid_angle)))
            my = int(cy - (r * 0.7) * sin(radians(mid_angle)))
            pct = (item["value"] / total) * 100
            self._add_hit_region(
                kind="pie",
                rect=QRect(cx - r, cy - r, r * 2, r * 2),
                center=QPoint(cx, cy),
                radius=r,
                hole_radius=hole_r,
                start_deg=start / 16.0,
                end_deg=(start + span) / 16.0,
                label=str(item.get("label", "")),
                raw_label=str(item.get("raw_label", "")),
                value=item["value"],
                percent=pct,
                anchor=QPoint(mx, my),
            )

            start += span

        # Donut hole
        painter.setBrush(QBrush(QColor(_INPUT_BG)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(cx - hole_r, cy - hole_r, hole_r * 2, hole_r * 2)

        # Center label: total count or single label
        if len(items) > 1:
            center_text = str(len(items)) + " cat"
        else:
            center_text = str(items[0].get("label", "")) if items else ""
        if center_text:
            font = _font(8)
            painter.setFont(font)
            painter.setPen(QColor(_TEXT_2))
            painter.drawText(
                cx - hole_r, cy - hole_r, hole_r * 2, hole_r * 2,
                Qt.AlignmentFlag.AlignCenter, center_text
            )

        font = _font(8)
        painter.setFont(font)
        lx = rect.right() - legend_w + 4
        ly = rect.top() + 8
        for i, item in enumerate(items):
            if i >= 6:
                break
            color = self._color_at(i, len(items))
            if i == active:
                color = color.lighter(140)
            painter.fillRect(lx, ly + i * 18, 10, 10, color)
            painter.setPen(QColor(_TEXT_2))
            lbl = QFontMetrics(font).elidedText(
                str(item.get("label", "")),
                Qt.TextElideMode.ElideRight,
                max(20, legend_w - 18),
            )
            painter.drawText(lx + 14, ly + i * 18 + 10, lbl)

    # ── Interaction ──────────────────────────────────────────────────────

    def _index_at(self, pos):
        """Return the index of the hit region under pos, or -1."""
        for i, region in enumerate(self._hit_regions):
            if region.get("kind") == "pie":
                if self._pie_region_contains(region, pos):
                    return i
                continue
            if region["rect"].contains(pos):
                return i
        return -1

    def _pie_region_contains(self, region, pos):
        from math import atan2, degrees, hypot

        center = region["center"]
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        distance = hypot(dx, dy)
        if distance < region["hole_radius"] or distance > region["radius"]:
            return False
        angle = (-degrees(atan2(dy, dx))) % 360
        start = region["start_deg"] % 360
        end = region["end_deg"] % 360
        if start <= end:
            return start <= angle <= end
        return angle >= start or angle <= end

    def mouseMoveEvent(self, event):
        self._cursor_pos = event.pos()
        idx = self._index_at(event.pos())
        if idx != self._hover_index:
            self._hover_index = idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if idx >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._cursor_pos = event.pos()
            idx = self._index_at(event.pos())
            if self._pinned_index == idx:
                self._pinned_index = -1
            elif idx >= 0:
                self._pinned_index = idx
            else:
                self._pinned_index = -1
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            # Copy the underlying data as TSV to the clipboard. Convenient
            # for the user to drop into a spreadsheet or note.
            rows = ["label\traw_label\tvalue"]
            for item in self.data:
                if isinstance(item, dict) and "label" in item and "value" in item:
                    rows.append(
                        f"{item['label']}\t{item.get('raw_label', '')}\t{item['value']}"
                    )
            if len(rows) > 1:
                QGuiApplication.clipboard().setText("\n".join(rows))

    def leaveEvent(self, event):
        if self._hover_index != -1:
            self._hover_index = -1
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()

    def mouseDoubleClickEvent(self, event):
        if self._pinned_index != -1:
            self._pinned_index = -1
            self.update()
