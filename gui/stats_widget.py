"""Statistics card widget — dark palette to match chat theme.

Shows key metrics in styled card format within the chat.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_SURFACE  = "#131316"
_INPUT_BG = "#1c1c20"
_BORDER   = "#27272a"
_TEXT     = "#fafafa"
_TEXT_3   = "#71717a"
_DANGER   = "#ef4444"


class StatsWidget(QFrame):
    """Card displaying layer statistics in a grid layout."""

    def __init__(self, stats_data, parent=None):
        super().__init__(parent)
        # Support both {"statistics": {...}} and the stats dict directly
        if "statistics" in stats_data:
            self.stats_data = stats_data["statistics"]
        else:
            self.stats_data = stats_data
        self._build_ui()

    def _build_ui(self):
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"""
            StatsWidget {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-top: 2px solid {_BORDER};
                border-radius: 8px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 10, 12, 10)

        layer_name = self.stats_data.get("layer_name", "Layer")
        title = QLabel(layer_name)
        title.setFont(QFont("Inter", 11, QFont.Bold))
        title.setStyleSheet(f"color: {_TEXT}; background: transparent;")
        main_layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(6)

        stats_list = []
        total_features = self.stats_data.get("total_features")
        if total_features is not None:
            stats_list.append(("Features", f"{total_features}", "#2196f3"))
        crs = self.stats_data.get("crs")
        if crs:
            stats_list.append(("CRS", crs, "#4caf50"))
        geometry_type = self.stats_data.get("geometry_type")
        if geometry_type is not None:
            type_names = {0: "Point", 1: "LineString", 2: "Polygon"}
            stats_list.append((
                "Geometry",
                type_names.get(geometry_type, f"Type {geometry_type}"),
                "#ff9800"
            ))
        distinct = self.stats_data.get("distinct_count")
        if distinct is not None:
            stats_list.append(("Distinct", f"{distinct}", "#9c27b0"))
        null_count = self.stats_data.get("null_count")
        if null_count is not None:
            stats_list.append(("Null", f"{null_count}", _DANGER))
        min_val = self.stats_data.get("min")
        if min_val is not None:
            stats_list.append(("Min", f"{min_val}", "#03a9f4"))
        max_val = self.stats_data.get("max")
        if max_val is not None:
            stats_list.append(("Max", f"{max_val}", "#8bc34a"))
        mean_val = self.stats_data.get("mean")
        if mean_val is not None:
            stats_list.append(("Mean", f"{mean_val:.2f}", "#ff9800"))
        sum_val = self.stats_data.get("sum")
        if sum_val is not None:
            stats_list.append(("Sum", f"{sum_val}", "#673ab7"))

        row = 0
        col = 0
        for label, value, color in stats_list:
            grid.addWidget(self._create_stat_card(label, value, color), row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        main_layout.addLayout(grid)

    def _create_stat_card(self, label, value, color):
        card = QFrame()
        card.setFrameShape(QFrame.NoFrame)
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 6px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setSpacing(2)
        layout.setContentsMargins(8, 6, 8, 6)

        label_widget = QLabel(label)
        label_widget.setStyleSheet(
            f"color: {_TEXT_3}; font-size: 9px; background: transparent;"
        )
        layout.addWidget(label_widget)

        value_widget = QLabel(str(value))
        value_widget.setStyleSheet(
            f"color: {color}; font-size: 15px; font-weight: bold; background: transparent;"
        )
        value_widget.setWordWrap(True)
        layout.addWidget(value_widget)

        return card
