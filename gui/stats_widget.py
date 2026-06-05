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

# Design tokens — darker, softer (match chat_dock.py)
_SURFACE  = "#161616"
_INPUT_BG = "#1e1e1e"
_BORDER   = "#2e2e2e"
_TEXT     = "#ececec"
_TEXT_2   = "#a0a0a0"
_TEXT_3   = "#707070"
_DANGER   = "#e57373"


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
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"""
            StatsWidget {{
                background-color: {_INPUT_BG};
                border: 1px solid {_BORDER};
                border-radius: 10px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(14, 12, 14, 12)

        layer_name = self.stats_data.get("layer_name", "Layer")
        title = QLabel(layer_name)
        title.setFont(QFont("Inter", 11, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {_TEXT}; background: transparent;")
        main_layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(6)

        stats_list = []
        total_features = self.stats_data.get("total_features")
        if total_features is not None:
            stats_list.append(("Features", f"{total_features}"))
        crs = self.stats_data.get("crs")
        if crs:
            stats_list.append(("CRS", crs))
        geometry_type = self.stats_data.get("geometry_type")
        if geometry_type is not None:
            type_names = {0: "Point", 1: "LineString", 2: "Polygon"}
            stats_list.append((
                "Geometry",
                type_names.get(geometry_type, f"Type {geometry_type}"),
            ))
        distinct = self.stats_data.get("distinct_count")
        if distinct is not None:
            stats_list.append(("Distinct", f"{distinct}"))
        null_count = self.stats_data.get("null_count")
        if null_count is not None:
            stats_list.append(("Null", f"{null_count}"))
        min_val = self.stats_data.get("min")
        if min_val is not None:
            stats_list.append(("Min", f"{min_val}"))
        max_val = self.stats_data.get("max")
        if max_val is not None:
            stats_list.append(("Max", f"{max_val}"))
        mean_val = self.stats_data.get("mean")
        if mean_val is not None:
            stats_list.append(("Mean", f"{mean_val:.2f}"))
        sum_val = self.stats_data.get("sum")
        if sum_val is not None:
            stats_list.append(("Sum", f"{sum_val}"))
        stdev_val = self.stats_data.get("stdev")
        if stdev_val is not None:
            stats_list.append(("StdDev", f"{stdev_val:.2f}"))

        row = 0
        col = 0
        for label, value in stats_list:
            grid.addWidget(self._create_stat_card(label, value), row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        main_layout.addLayout(grid)

    def _create_stat_card(self, label, value):
        card = QFrame()
        card.setFrameShape(QFrame.Shape.NoFrame)
        card.setStyleSheet(f"""
            QFrame {{
                background-color: {_SURFACE};
                border: 1px solid {_BORDER};
                border-radius: 6px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setSpacing(2)
        layout.setContentsMargins(10, 8, 10, 8)

        label_widget = QLabel(label)
        label_widget.setStyleSheet(
            f"color: {_TEXT_3}; font-size: 9px; background: transparent; letter-spacing: 0.03em;"
        )
        layout.addWidget(label_widget)

        value_widget = QLabel(str(value))
        value_widget.setStyleSheet(
            f"color: {_TEXT_2}; font-size: 14px; font-weight: 500; background: transparent;"
        )
        value_widget.setWordWrap(True)
        layout.addWidget(value_widget)

        return card
