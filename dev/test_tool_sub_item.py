import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import ToolSubItem

win = QWidget()
win.setWindowTitle("ToolSubItem test")
win.setStyleSheet("background:#1c1c1c;")
win.resize(600, 100)
lay = QVBoxLayout(win)

item1 = ToolSubItem({"layer": "roads_2024"}, group=None, is_last=False)
item2 = ToolSubItem({"layer": "buildings"}, group=None, is_last=True)
lay.addWidget(item1)
lay.addWidget(item2)

QTimer.singleShot(1500, lambda: item1.mark_done(is_error=False))
QTimer.singleShot(2500, lambda: item2.mark_done(is_error=True))

win.show()
sys.exit(app.exec_())
