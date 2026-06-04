"""Regression check for animated temporary progress text in agent turns."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.gui.agent_turn_bubble import AgentTurnBubble, ReasoningTicker
from AgenticGis.gui.tool_call_bubble import ToolCallBubble


def main():
    app = QApplication.instance() or QApplication([])
    turn = AgentTurnBubble()

    turn.set_progress_text("Thinking...")
    first = turn.text_lbl.text()
    assert "Thinking" in first
    assert turn._progress_timer.isActive()

    turn._render_progress_text()
    second = turn.text_lbl.text()
    assert second != first, "progress text should animate between timer ticks"
    assert "⠋" in first
    assert "⠙" in second

    turn.set_streaming_text("Hi. How can I help?")
    assert not turn._progress_timer.isActive()
    assert "Thinking" not in turn.text_lbl.text()
    assert "Hi. How can I help?" in turn.text_lbl.text()

    item = turn.add_tool("run_processing", {"alg_id": "native:buffer"})
    group = turn._groups["run_processing"]
    tool_first = group._dot_lbl.text()
    group._tick_running()
    tool_second = group._dot_lbl.text()
    assert tool_first != tool_second
    assert "processing" in group._state_lbl.text()
    item.set_result("ok", False)
    assert group._dot_lbl.text() == "✓"

    turn.deleteLater()

    ticker = ReasoningTicker()
    ticker.append("checking loaded layers")
    ticker_first = ticker._prefix_lbl.text()
    ticker._tick()
    ticker_second = ticker._prefix_lbl.text()
    assert ticker_first != ticker_second
    assert ticker._timer.isActive()
    ticker.hide_ticker()
    assert not ticker._timer.isActive()
    ticker.deleteLater()

    legacy = ToolCallBubble("run_pyqgis", {"code": "result = 1"})
    legacy_first = legacy.status_label.text()
    legacy._animate_dots()
    legacy_second = legacy.status_label.text()
    assert legacy_first != legacy_second
    assert legacy.state_label.text() == "processing"
    legacy.set_result("ok", False)
    assert legacy.status_label.text() == "✓"
    legacy.deleteLater()

    app.processEvents()


if __name__ == "__main__":
    main()
