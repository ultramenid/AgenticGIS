import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import ToolGroupRow

win = QWidget()
win.setWindowTitle("ToolGroupRow test")
win.setStyleSheet("background:#1c1c1c;")
win.resize(600, 160)
lay = QVBoxLayout(win)

group = ToolGroupRow("read_layer")
lay.addWidget(group)

item1 = group.add_item({"layer": "roads_2024"})
item2 = group.add_item({"layer": "buildings"})
item3 = group.add_item({"layer": "parks"})

QTimer.singleShot(800,  lambda: item1.set_result("ok", False))
QTimer.singleShot(1400, lambda: item2.set_result("ok", False))
QTimer.singleShot(2000, lambda: item3.set_result("ok", False))

win.show()
sys.exit(app.exec_())
