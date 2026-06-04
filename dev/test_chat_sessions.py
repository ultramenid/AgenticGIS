"""Regression checks for chat dock session switching and restore."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from qgis.PyQt.QtWidgets import QApplication

from AgenticGis.core.session_store import SessionStore
from AgenticGis.gui.chat_dock import ChatDock


class _Settings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class _FakeSignal:
    def disconnect(self):
        pass


class _FakeWorker:
    def __init__(self):
        self.stopped = False
        self.event = _FakeSignal()
        self.finished_history = _FakeSignal()
        self.finished = _FakeSignal()

    def stop(self):
        self.stopped = True


class _Backend:
    def __init__(self):
        self.state = {}

    def export_session_state(self):
        return dict(self.state)

    def import_session_state(self, state):
        self.state = dict(state or {})


def _dock(store=None, backend=None):
    app = QApplication.instance() or QApplication([])
    backend = backend or _Backend()
    dock = ChatDock(
        lambda: backend,
        lambda: None,
        lambda: None,
        session_store=store or SessionStore(settings=_Settings()),
        show_startup_picker=False,
    )
    return app, dock, backend


def test_header_uses_session_menu_instead_of_clear():
    app, dock, _backend = _dock()

    assert "Session" in dock._session_btn.text()
    assert not hasattr(dock, "_clear_btn")
    assert [action.text() for action in dock._session_menu.actions()] == [
        "New session",
        "Session list",
        "Rename current",
        "Delete current",
    ]

    dock.deleteLater()
    app.processEvents()


def test_session_switching_preserves_separate_history_and_backend_state():
    store = SessionStore(settings=_Settings())
    first = store.active_session()
    second = store.create_session("Second")
    store.save_session(
        first["id"],
        backend_history=[{"role": "user", "content": "first"}],
        transcript_events=[{"type": "user", "text": "first"}],
        backend_state={"session_id": "sid-first"},
    )
    store.save_session(
        second["id"],
        backend_history=[{"role": "user", "content": "second"}],
        transcript_events=[{"type": "user", "text": "second"}],
        backend_state={"session_id": "sid-second"},
    )
    backend = _Backend()
    app, dock, _backend = _dock(store=store, backend=backend)

    dock._switch_to_session(first["id"])
    assert dock._history[0]["content"] == "first"
    assert backend.state["session_id"] == "sid-first"

    dock._history = [{"role": "assistant", "content": "changed"}]
    backend.state = {"session_id": "sid-updated"}
    dock._switch_to_session(second["id"])

    saved_first = store.get_session(first["id"])
    assert saved_first["backend_history"][0]["content"] == "changed"
    assert saved_first["backend_state"]["session_id"] == "sid-updated"
    assert dock._history[0]["content"] == "second"
    assert backend.state["session_id"] == "sid-second"

    dock.deleteLater()
    app.processEvents()


def test_switch_stops_running_worker_before_restoring_session():
    store = SessionStore(settings=_Settings())
    target = store.create_session("Target")
    app, dock, _backend = _dock(store=store)
    worker = _FakeWorker()
    dock._worker = worker

    dock._switch_to_session(target["id"])

    assert worker.stopped is True
    assert dock._stop_requested is True

    dock.deleteLater()
    app.processEvents()


def test_restore_replays_completed_records_and_skips_live_state():
    app, dock, _backend = _dock()

    dock._restore_transcript([
        {"type": "user", "text": "Question"},
        {
            "type": "agent_turn",
            "thinking": "Checked layer",
            "tools": [
                {
                    "name": "analyze_layer",
                    "input": {"layer": "roads"},
                    "result": "ok",
                    "is_error": False,
                }
            ],
            "text": "Answer",
        },
        {"type": "chart", "data": {"chart_type": "bar", "data": [{"label": "A", "value": 1}]}},
        {"type": "stats", "data": {"layer_name": "roads", "total_features": 1}},
        {"type": "error", "text": "Boom"},
        {"type": "compaction"},
        {"type": "typing"},
        {"type": "ask_user", "question": "live"},
    ])

    assert dock._typing_widget is None
    assert dock._ask_user_card is None
    assert dock._current_agent_turn is None
    assert dock.transcript_layout.count() == 7  # six restored widgets + trailing stretch

    dock.deleteLater()
    app.processEvents()


def main():
    test_header_uses_session_menu_instead_of_clear()
    test_session_switching_preserves_separate_history_and_backend_state()
    test_switch_stops_running_worker_before_restoring_session()
    test_restore_replays_completed_records_and_skips_live_state()


if __name__ == "__main__":
    main()
