"""Statistics card widget — dark palette to match chat theme.

Shows key metrics in styled card format within the chat.
"""

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)

from .downloadable import HoverDownloadButton, save_csv, _safe_name
from .formatting import localize_decimal
from .theme import (
    fs,
    ui_font,
    DOCK_SURFACE as _SURFACE,
    DOCK_SURFACE as _INPUT_BG,
    DOCK_BORDER as _BORDER,
    DOCK_TEXT as _TEXT,
    DOCK_TEXT_2 as _TEXT_2,
    DOCK_TEXT_3 as _TEXT_3,
)

# Design tokens — darker, softer (match chat_dock.py)


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
                border-radius: 12px;
            }}
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(14, 12, 14, 12)

        layer_name = self.stats_data.get("layer_name", QCoreApplication.translate("StatsWidget", "Layer"))
        title = QLabel(layer_name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setFont(ui_font(13, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {_TEXT}; background: transparent;")
        main_layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(6)

        stats_list = []
        self._stats_rows = []

        def add_stat(export_label, display_label, value, text=None):
            # Stable CSV metric captions and numbers are part of the export.
            self._stats_rows.append((export_label, str(value) if text is None else text))
            display_value = str(value) if text is None else text
            if isinstance(value, float):
                display_value = localize_decimal(display_value)
            stats_list.append((display_label, display_value))
        total_features = self.stats_data.get("total_features")
        if total_features is not None:
            add_stat("Features", QCoreApplication.translate("StatsWidget", "Features"), total_features)
        crs = self.stats_data.get("crs")
        if crs:
            add_stat("CRS", QCoreApplication.translate("StatsWidget", "CRS"), crs)
        geometry_type = self.stats_data.get("geometry_type")
        if geometry_type is not None:
            type_names = {0: "Point", 1: "LineString", 2: "Polygon"}
            display_types = {
                0: QCoreApplication.translate("StatsWidget", "Point"),
                1: QCoreApplication.translate("StatsWidget", "LineString"),
                2: QCoreApplication.translate("StatsWidget", "Polygon"),
            }
            value = type_names.get(geometry_type, f"Type {geometry_type}")
            self._stats_rows.append(("Geometry", value))
            display_value = display_types.get(geometry_type) or QCoreApplication.translate("StatsWidget", "Type {number}").format(
                number=geometry_type
            )
            stats_list.append((QCoreApplication.translate("StatsWidget", "Geometry"), display_value))
        distinct = self.stats_data.get("distinct_count")
        if distinct is not None:
            add_stat("Distinct", QCoreApplication.translate("StatsWidget", "Distinct"), distinct)
        null_count = self.stats_data.get("null_count")
        if null_count is not None:
            add_stat("Null", QCoreApplication.translate("StatsWidget", "Null"), null_count)
        min_val = self.stats_data.get("min")
        if min_val is not None:
            add_stat("Min", QCoreApplication.translate("StatsWidget", "Min"), min_val)
        max_val = self.stats_data.get("max")
        if max_val is not None:
            add_stat("Max", QCoreApplication.translate("StatsWidget", "Max"), max_val)
        mean_val = self.stats_data.get("mean")
        if mean_val is not None:
            add_stat("Mean", QCoreApplication.translate("StatsWidget", "Mean"), mean_val, f"{mean_val:.2f}")
        sum_val = self.stats_data.get("sum")
        if sum_val is not None:
            add_stat("Sum", QCoreApplication.translate("StatsWidget", "Sum"), sum_val)
        stdev_val = self.stats_data.get("stdev")
        if stdev_val is not None:
            add_stat("StdDev", QCoreApplication.translate("StatsWidget", "StdDev"), stdev_val, f"{stdev_val:.2f}")

        row = 0
        col = 0
        for label, value in stats_list:
            grid.addWidget(self._create_stat_card(label, value), row, col)
            col += 1
            if col >= 2:
                col = 0
                row += 1

        main_layout.addLayout(grid)

        # Hover-to-download: save the metrics as CSV.
        HoverDownloadButton(self, self._save, tooltip=QCoreApplication.translate("StatsWidget", "Save stats (CSV)"))

    def _save(self):
        layer_name = self.stats_data.get("layer_name", "Layer")
        rows = [["Metric", "Value"]]
        rows.extend([label, value] for label, value in self._stats_rows)
        save_csv(self, rows, _safe_name(layer_name, "stats", ".csv"))

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
        label_widget.setWordWrap(True)
        label_widget.setStyleSheet(
            f"color: {_TEXT_3}; font-size: {fs(9)}px; background: transparent; letter-spacing: 0.03em;"
        )
        layout.addWidget(label_widget)

        value_widget = QLabel(str(value))
        value_widget.setTextFormat(Qt.TextFormat.PlainText)
        value_widget.setStyleSheet(
            f"color: {_TEXT_2}; font-size: {fs(14)}px; font-weight: 500; background: transparent;"
        )
        value_widget.setWordWrap(True)
        layout.addWidget(value_widget)

        return card
