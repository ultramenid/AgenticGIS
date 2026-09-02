"""Discovery registry for external MCP clients (QGIS-free, stdlib only).

The in-QGIS bridge binds a localhost port and records it here so outside
agent CLIs (Claude Code, Codex, OpenCode, …) can find the live QGIS
session without hard-coding a port. The file lives at
``~/.agenticgis/mcp.json`` (override with the ``AGENTICGIS_MCP_DISCOVERY``
environment variable) and holds a list of entries:

    {"servers": [{"url": "http://127.0.0.1:7317/mcp", "pid": 12345,
                  "host": "127.0.0.1", "port": 7317}]}

Entries are keyed by the QGIS process id: the plugin registers on startup
and unregisters on unload, and dead-process entries are pruned on every
write. ``server/mcp_stdio.py`` consumes this file when no explicit URL is
given, so this module must stay importable outside QGIS (no qgis imports,
any Python >= 3.9).
"""

import json
import os
import tempfile

ENV_VAR = "AGENTICGIS_MCP_DISCOVERY"


def discovery_path():
    """Return the discovery-file path (overridable for tests)."""
    override = os.environ.get(ENV_VAR)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".agenticgis", "mcp.json")


def pid_alive(pid):
    """Return True when *pid* refers to a live process.

    On Windows ``os.kill(pid, 0)`` would *terminate* the process
    (``TerminateProcess``), so there we conservatively report "alive" —
    the client-side HTTP probe in the stdio proxy is the real liveness
    check anyway.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows safety; see docstring
        return True
    try:
        os.kill(pid, 0)
    except PermissionError:  # exists but owned by another user
        return True
    except OSError:
        return False
    return True


def read_servers():
    """Return the registered server entries (empty list on any error)."""
    try:
        with open(discovery_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    entries = data.get("servers") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    return [dict(entry) for entry in entries if isinstance(entry, dict)]


def _write_servers(entries):
    """Atomically rewrite the discovery file (tmp file + os.replace)."""
    path = discovery_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".mcp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"servers": entries}, fh)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _prune_dead(entries):
    return [entry for entry in entries if pid_alive(entry.get("pid"))]


def register_server(entry):
    """Add/replace this process's entry, dropping dead-process entries."""
    entries = _prune_dead(read_servers())
    pid = entry.get("pid")
    if pid is not None:
        entries = [e for e in entries if e.get("pid") != pid]
    entries.append(dict(entry))
    _write_servers(entries)


def unregister_server(pid):
    """Remove the entry for *pid* (no-op when absent)."""
    if pid is None:
        return
    entries = _prune_dead([e for e in read_servers() if e.get("pid") != pid])
    _write_servers(entries)


def live_servers():
    """Return registered entries whose process is alive."""
    return _prune_dead(read_servers())
