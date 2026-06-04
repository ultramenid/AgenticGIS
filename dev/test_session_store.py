"""Regression checks for persistent chat session storage."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from AgenticGis.core.session_store import SessionStore


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def test_creates_default_session_when_empty():
    store = SessionStore(settings=_Settings())

    session = store.active_session()

    assert session["name"] == "New session"
    assert session["backend_history"] == []
    assert session["transcript_events"] == []


def test_persists_and_reloads_named_session():
    settings = _Settings()
    store = SessionStore(settings=settings)
    session = store.create_session("Flood map")
    store.save_session(
        session["id"],
        backend_history=[{"role": "user", "content": "map it"}],
        transcript_events=[{"type": "user", "text": "map it"}],
        backend_state={"session_id": "cli-123"},
    )

    reloaded = SessionStore(settings=settings)
    active = reloaded.active_session()

    assert active["id"] == session["id"]
    assert active["name"] == "Flood map"
    assert active["backend_history"][0]["content"] == "map it"
    assert active["transcript_events"][0]["text"] == "map it"
    assert active["backend_state"]["session_id"] == "cli-123"


def test_renames_session_and_updates_timestamp():
    store = SessionStore(settings=_Settings())
    session = store.active_session()
    before = session["updated_at"]

    store.rename_session(session["id"], "Renamed")
    renamed = store.get_session(session["id"])

    assert renamed["name"] == "Renamed"
    assert renamed["updated_at"] >= before


def test_trims_to_latest_twenty_sessions():
    store = SessionStore(settings=_Settings(), limit=20)
    first_id = store.active_session()["id"]
    for index in range(25):
        store.create_session(f"Session {index}")

    sessions = store.list_sessions()

    assert len(sessions) == 20
    assert first_id not in [session["id"] for session in sessions]
    assert sessions[0]["name"] == "Session 24"


def test_corrupt_json_creates_fresh_session_without_crashing():
    store = SessionStore(settings=_Settings({"AgenticGIS/sessions_json": "{not json"}))

    assert len(store.list_sessions()) == 1
    assert store.active_session()["name"] == "New session"


def main():
    test_creates_default_session_when_empty()
    test_persists_and_reloads_named_session()
    test_renames_session_and_updates_timestamp()
    test_trims_to_latest_twenty_sessions()
    test_corrupt_json_creates_fresh_session_without_crashing()


if __name__ == "__main__":
    main()
