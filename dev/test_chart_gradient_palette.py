"""Regression checks for default chart gradient and custom color override."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.chart_widget import ChartWidget


def main():
    app = QApplication.instance() or QApplication([])

    widget = ChartWidget({
        "chart_type": "bar",
        "data": [
            {"label": "One", "value": 1},
            {"label": "Two", "value": 2},
            {"label": "Three", "value": 3},
        ],
    })

    first = widget._color_at(0, 3).name()
    middle = widget._color_at(1, 3).name()
    last = widget._color_at(2, 3).name()
    assert first == "#79a883"
    assert last == "#d9a35f"
    assert middle not in (first, last)

    custom = ChartWidget({
        "chart_type": "bar",
        "colors": ["#123456"],
        "data": [{"label": "One", "value": 1}, {"label": "Two", "value": 2}],
    })
    assert custom._color_at(0, 2).name() == "#123456"
    assert custom._color_at(1, 2).name() == "#123456"

    widget.deleteLater()
    custom.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
