"""Regression check for expanding and collapsing tool details."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.agent_turn_bubble import ToolRowWidget


def main():
    app = QApplication.instance() or QApplication([])
    row = ToolRowWidget("run_pyqgis", {"code": "result = 1"})
    row.set_result('{"ok": true, "result": 1}', False)

    row._toggle()
    app.processEvents()
    row._toggle()
    app.processEvents()

    row.deleteLater()
    app.processEvents()


if __name__ == "__main__":
    main()
