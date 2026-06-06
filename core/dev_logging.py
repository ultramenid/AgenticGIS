"""Optional JSONL development logging for bottleneck tracing.

Disabled by default. Enable with:

    AGENTICGIS_DEV_LOG=1

Optional path override:

    AGENTICGIS_DEV_LOG_PATH=/path/to/agenticgis-dev.log
"""

import json
import os
import secrets
import threading
import time
from contextlib import contextmanager

_LOCK = threading.Lock()
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
_DEFAULT_LOG_PATH = os.path.join(_PROJECT_ROOT, "agenticgis-dev.log")
_DOTENV = None


def _parse_dotenv_line(line):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None, None
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None, None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def _dotenv_values():
    global _DOTENV
    if _DOTENV is not None:
        return _DOTENV
    values = {}
    try:
        with open(_ENV_PATH, "r", encoding="utf-8") as handle:
            for line in handle:
                key, value = _parse_dotenv_line(line)
                if key:
                    values[key] = value
    except OSError:
        pass
    _DOTENV = values
    return values


def _config_value(key, default=""):
    return os.environ.get(key, _dotenv_values().get(key, default))


def enabled():
    value = _config_value("AGENTICGIS_DEV_LOG")
    return value.lower() in ("1", "true", "yes", "on")


def log_path():
    env_path = os.environ.get("AGENTICGIS_DEV_LOG_PATH")
    if env_path:
        return env_path
    path = _dotenv_values().get("AGENTICGIS_DEV_LOG_PATH", _DEFAULT_LOG_PATH)
    if not os.path.isabs(path):
        return os.path.abspath(os.path.join(_PROJECT_ROOT, path))
    return path


def _safe(value):
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def log_event(event, **fields):
    """Append one structured event if development logging is enabled."""
    if not enabled():
        return
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "event": event,
        "thread": threading.current_thread().name,
    }
    record.update({key: _safe(value) for key, value in fields.items()})
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    try:
        path = log_path()
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        # Logging must never affect QGIS/plugin behavior.
        return


def new_trace_id():
    """Return a short opaque identifier for correlating one user send."""
    return secrets.token_hex(4)


def log_ttft_event(stage, trace_id, started_at):
    """Record one content-free TTFT milestone using monotonic elapsed time."""
    if not trace_id or started_at is None:
        return
    try:
        elapsed_ms = max(0, int((time.monotonic() - started_at) * 1000))
        log_event(
            f"ttft.{stage}",
            trace_id=str(trace_id),
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        # Instrumentation must never affect QGIS/plugin behavior.
        return


@contextmanager
def timed(event, **fields):
    start = time.perf_counter()
    log_event(f"{event}.start", **fields)
    try:
        yield
    except BaseException as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_event(
            f"{event}.error",
            elapsed_ms=elapsed_ms,
            error_type=type(exc).__name__,
            **fields,
        )
        raise
    else:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        log_event(f"{event}.end", elapsed_ms=elapsed_ms, **fields)
