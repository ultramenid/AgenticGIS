"""Persistent chat session storage backed by QSettings."""

import json
import uuid
from datetime import datetime, timezone

from qgis.PyQt.QtCore import QSettings, QTimer


SETTINGS_KEY = "AgenticGIS/sessions_json"
SCHEMA_VERSION = 1
DEFAULT_SESSION_NAME = "New session"
WARN_SESSION_BYTES = 1_000_000


def _now():
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Small JSON store for global AgenticGIS chat sessions."""

    def __init__(self, settings=None, limit=20):
        self._settings = settings or QSettings()
        self._limit = int(limit or 20)
        self.had_existing_sessions = False
        self._pending_save = None
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_scheduled_save)
        self._data = self._load()
        if not self._data.get("sessions"):
            self.create_session(DEFAULT_SESSION_NAME)
        else:
            self._normalize()
            self._persist()

    def _load(self):
        raw = self._settings.value(SETTINGS_KEY, "")
        if not raw:
            return {"schema_version": SCHEMA_VERSION, "active_session_id": None, "sessions": []}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {"schema_version": SCHEMA_VERSION, "active_session_id": None, "sessions": []}
        sessions = data.get("sessions") if isinstance(data, dict) else None
        if not isinstance(sessions, list):
            return {"schema_version": SCHEMA_VERSION, "active_session_id": None, "sessions": []}
        self.had_existing_sessions = bool(sessions)
        return {
            "schema_version": SCHEMA_VERSION,
            "active_session_id": data.get("active_session_id"),
            "sessions": sessions,
        }

    def _normalize(self):
        sessions = []
        for session in self._data.get("sessions", []):
            if not isinstance(session, dict):
                continue
            sid = str(session.get("id") or uuid.uuid4().hex)
            name = str(session.get("name") or DEFAULT_SESSION_NAME)
            created = str(session.get("created_at") or _now())
            updated = str(session.get("updated_at") or created)
            sessions.append({
                "id": sid,
                "name": name,
                "created_at": created,
                "updated_at": updated,
                "backend_history": self._list_or_empty(session.get("backend_history")),
                "transcript_events": self._list_or_empty(session.get("transcript_events")),
                "backend_state": self._dict_or_empty(session.get("backend_state")),
            })
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        self._data["sessions"] = sessions[:self._limit]
        ids = {session["id"] for session in self._data["sessions"]}
        active_id = self._data.get("active_session_id")
        if active_id not in ids:
            self._data["active_session_id"] = self._data["sessions"][0]["id"] if self._data["sessions"] else None

    @staticmethod
    def _list_or_empty(value):
        return value if isinstance(value, list) else []

    @staticmethod
    def _dict_or_empty(value):
        return value if isinstance(value, dict) else {}

    def _persist(self):
        self._normalize()
        self._settings.setValue(SETTINGS_KEY, json.dumps(self._data, separators=(",", ":")))

    def schedule_save(
        self,
        session_id,
        backend_history=None,
        transcript_events=None,
        backend_state=None,
    ):
        self._pending_save = {
            "session_id": session_id,
            "backend_history": backend_history,
            "transcript_events": transcript_events,
            "backend_state": backend_state,
        }
        self._save_timer.stop()
        self._save_timer.start(1500)

    def flush_save(self):
        self._save_timer.stop()
        self._flush_scheduled_save()

    def _flush_scheduled_save(self):
        if self._pending_save is None:
            return
        self.save_session(**self._pending_save)
        self._pending_save = None

    def list_sessions(self):
        self._normalize()
        return [self._with_size_metadata(session) for session in self._data["sessions"]]

    def get_session(self, session_id):
        for session in self._data.get("sessions", []):
            if session.get("id") == session_id:
                return self._with_size_metadata(session)
        return None

    @staticmethod
    def _session_size_bytes(session):
        return len(json.dumps(session, separators=(",", ":"), default=str).encode("utf-8"))

    @classmethod
    def _with_size_metadata(cls, session):
        item = dict(session)
        size = cls._session_size_bytes(session)
        item["size_bytes"] = size
        item["size_warning"] = size >= WARN_SESSION_BYTES
        return item

    def active_session(self):
        active_id = self._data.get("active_session_id")
        session = self.get_session(active_id)
        if session is None:
            session = self.create_session(DEFAULT_SESSION_NAME)
        return session

    def create_session(self, name=None):
        stamp = _now()
        session = {
            "id": uuid.uuid4().hex,
            "name": (name or DEFAULT_SESSION_NAME).strip() or DEFAULT_SESSION_NAME,
            "created_at": stamp,
            "updated_at": stamp,
            "backend_history": [],
            "transcript_events": [],
            "backend_state": {},
        }
        self._data.setdefault("sessions", []).append(session)
        self._data["active_session_id"] = session["id"]
        self._persist()
        return dict(session)

    def set_active_session(self, session_id):
        if self.get_session(session_id) is None:
            return False
        self._data["active_session_id"] = session_id
        self._persist()
        return True

    def save_session(self, session_id, backend_history=None, transcript_events=None, backend_state=None):
        for session in self._data.get("sessions", []):
            if session.get("id") != session_id:
                continue
            if backend_history is not None:
                session["backend_history"] = self._list_or_empty(backend_history)
            if transcript_events is not None:
                session["transcript_events"] = self._list_or_empty(transcript_events)
            if backend_state is not None:
                session["backend_state"] = self._dict_or_empty(backend_state)
            session["updated_at"] = _now()
            self._persist()
            return dict(session)
        return None

    def rename_session(self, session_id, name):
        clean = (name or DEFAULT_SESSION_NAME).strip() or DEFAULT_SESSION_NAME
        for session in self._data.get("sessions", []):
            if session.get("id") == session_id:
                session["name"] = clean
                session["updated_at"] = _now()
                self._persist()
                return dict(session)
        return None

    def delete_session(self, session_id):
        before = len(self._data.get("sessions", []))
        self._data["sessions"] = [
            session for session in self._data.get("sessions", [])
            if session.get("id") != session_id
        ]
        if len(self._data["sessions"]) == before:
            return None
        if not self._data["sessions"]:
            fallback = self.create_session(DEFAULT_SESSION_NAME)
            return fallback
        self._normalize()
        if self._data.get("active_session_id") == session_id:
            self._data["active_session_id"] = self._data["sessions"][0]["id"]
        self._persist()
        return self.active_session()
