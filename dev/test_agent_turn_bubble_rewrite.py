import sys
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.PyQt.QtCore import QTimer

app = QApplication.instance() or QApplication(sys.argv)
from gui.agent_turn_bubble import AgentTurnBubble

win = QWidget()
win.setWindowTitle("AgentTurnBubble rewrite")
win.setStyleSheet("background:#141414;")
win.resize(700, 420)
lay = QVBoxLayout(win)
bubble = AgentTurnBubble()
lay.addWidget(bubble)

t = [0]

def step():
    n = t[0]
    t[0] += 1
    if n == 0:
        bubble.set_thinking_text("considering layer boundaries to filter by extent")
    elif n == 1:
        bubble.set_thinking_text(
            "considering layer boundaries to filter by extent of the selected region…"
        )
    elif n == 2:
        r1 = bubble.add_tool("read_layer", {"layer": "roads_2024"})
        r2 = bubble.add_tool("read_layer", {"layer": "buildings"})
        r3 = bubble.add_tool("read_layer", {"layer": "parks"})
        QTimer.singleShot(700,  lambda: r1.set_result("ok", False))
        QTimer.singleShot(1200, lambda: r2.set_result("ok", False))
        QTimer.singleShot(1800, lambda: r3.set_result("ok", False))
    elif n == 4:
        q = bubble.add_tool("run_query", {"query": "SELECT * FROM roads WHERE speed > 80"})
        QTimer.singleShot(900, lambda: q.set_result("142 rows", False))
    elif n == 7:
        bubble.set_streaming_text("The analysis found ")
    elif n == 8:
        bubble.set_streaming_text("The analysis found 142 road segments ")
    elif n == 9:
        bubble.finalize_text(
            "The analysis found **142 road segments** intersecting the selected area."
        )
    elif n == 10:
        bubble.finalize()
        timer.stop()

timer = QTimer()
timer.setInterval(700)
timer.timeout.connect(step)
timer.start()

win.show()
sys.exit(app.exec_())
