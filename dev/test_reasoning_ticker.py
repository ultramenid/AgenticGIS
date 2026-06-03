import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import ReasoningTicker

win = QWidget()
win.setWindowTitle("ReasoningTicker test")
win.setStyleSheet("background:#1c1c1c;")
win.resize(600, 60)
lay = QVBoxLayout(win)
ticker = ReasoningTicker()
lay.addWidget(ticker)

chunks = ["considering ", "layer ", "boundaries ", "to filter ", "by spatial ", "extent…"]
idx = [0]

def send():
    if idx[0] < len(chunks):
        ticker.append(chunks[idx[0]])
        idx[0] += 1

t = QTimer()
t.setInterval(400)
t.timeout.connect(send)
t.start()

win.show()
sys.exit(app.exec_())
