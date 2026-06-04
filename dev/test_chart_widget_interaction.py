"""Regression checks for ChartWidget hit testing."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtCore import QPoint
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.chart_widget import ChartWidget


def main():
    app = QApplication.instance() or QApplication([])

    widget = ChartWidget(
        {
            "chart_type": "pie",
            "data": [
                {"label": "North", "value": 10},
                {"label": "South", "value": 10},
            ],
        }
    )
    widget.resize(400, 260)

    pixmap = QPixmap(widget.size())
    widget.render(pixmap)

    assert len(widget._hit_regions) == 2
    first = widget._hit_regions[0]
    second = widget._hit_regions[1]
    center = first["center"]
    radius = first["radius"]

    top_slice = QPoint(center.x(), int(center.y() - radius * 0.7))
    bottom_slice = QPoint(center.x(), int(center.y() + radius * 0.7))
    hole = QPoint(center.x(), center.y())

    assert widget._index_at(top_slice) == 0
    assert widget._index_at(bottom_slice) == 1
    assert widget._index_at(hole) == -1

    widget.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
