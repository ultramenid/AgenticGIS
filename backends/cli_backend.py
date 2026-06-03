"""Drive an installed, already-logged-in agent CLI (Claude Code / OpenCode).

The plugin hosts a local MCP server (see ``server.mcp_server``); this backend
spawns the CLI in headless mode pointed at that server, so the CLI's own agent
loop calls the QGIS tools. Because the CLI is already authenticated, no API
key is needed. We stream the CLI's stdout back into the chat dock.

Conversation continuity is handled per-tool: Claude Code returns a
``session_id`` we ``--resume`` on later turns.

Reliability hardening
---------------------
* Robust JSONL parsing — read raw bytes and split on ``\\n`` ourselves, so
  multi-line JSON records (or two records in one ``write()``) survive.
* Single select()-driven reader so a verbose CLI cannot deadlock us by
  filling the 64 kB pipe buffer. We never spawn two blocking reader
  threads.
* ``start_new_session=True`` so Ctrl-C in the spawned CLI doesn't kill
  QGIS, and so child file descriptors don't leak.
* A bounded ``kill_timeout`` (5 s) before we escalate to ``SIGKILL`` if
  the process refuses to terminate cleanly.
"""

import json
import os
import select
import shutil
import subprocess
import threading

from .base import AgentBackend, AgentEvent, EventType

# Fallback locations discovered on this machine, used if PATH lookup fails.
_KNOWN_PATHS = {
    "claude": [
        "/Applications/cmux.app/Contents/Resources/bin/claude",
        os.path.expanduser("~/.claude/local/claude"),
    ],
    "opencode": [
        os.path.expanduser("~/.opencode/bin/opencode"),
    ],
    "codex": [
        os.path.expanduser("~/.codex/bin/codex"),
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
    ],
    "gemini": [
        os.path.expanduser("~/.gemini/bin/gemini"),
        "/opt/homebrew/bin/gemini",
        "/usr/local/bin/gemini",
    ],
}


def _resolve_binary(tool, explicit_path):
    if explicit_path and os.path.exists(explicit_path):
        return explicit_path
    found = shutil.which(tool)
    if found:
        return found
    for candidate in _KNOWN_PATHS.get(tool, []):
        if os.path.exists(candidate):
            return candidate
    return None


class CliToolBackend(AgentBackend):
    def __init__(self, config, server_provider):
        self.config = config
        self.tool = config.get("cli_tool")
        self.binary = _resolve_binary(self.tool, config.get("cli_path"))
        # Zero-arg callable returning the running MCP server's base URL.
        self._server_provider = server_provider
        # F15: synchronise mutations of _proc and _session_id across the
        # dock and the worker thread.
        self._lock = threading.Lock()
        self._proc = None
        self._session_id = None

    @property
    def label(self):
        return f"CLI ({self.tool})"

    def validate(self):
        if self.binary is None:
            return (f"Could not find the '{self.tool}' binary. Set its path in "
                    "Settings, or make sure it is on PATH.")
        if self._server_provider is None or self._server_provider() is None:
            return "The local MCP bridge could not start."
        return None

    # ------------------------------------------------------------------ #
    # Login / auth helpers
    # ------------------------------------------------------------------ #

    def _check_login_cmd(self):
        """Return the command to check authentication status."""
        if self.tool in ("claude", "opencode", "codex", "gemini"):
            return [self.binary, "status"]
        return [self.binary, "status"]

    def check_login(self):
        """Return True if the CLI reports an active session."""
        if not self.binary:
            return False
        try:
            result = subprocess.run(
                self._check_login_cmd(),
                capture_output=True, text=True, timeout=8,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    def _login_cmd(self):
        """Return the command to open a browser login flow."""
        if self.tool in ("claude", "opencode", "codex", "gemini"):
            return [self.binary, "login"]
        return [self.binary, "login"]

    def login_browser(self):
        """Launch the browser-based login flow."""
        if not self.binary:
            return False
        try:
            subprocess.Popen(
                self._login_cmd(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    def _mcp_config_json(self, base_url):
        return json.dumps({
            "mcpServers": {
                "AgenticGIS": {"type": "http", "url": base_url}
            }
        })

    def _build_command(self, message, base_url):
        if self.tool == "claude":
            cmd = [
                self.binary, "-p", message,
                "--output-format", "stream-json", "--verbose",
                "--mcp-config", self._mcp_config_json(base_url),
                "--permission-mode", "bypassPermissions",
                "--allowedTools", "mcp__AgenticGIS",
            ]
            if self._session_id:
                cmd += ["--resume", self._session_id]
            return cmd
        if self.tool == "opencode":
            return [self.binary, "run", message]
        if self.tool == "codex":
            # Codex CLI (beta) — headless mode with MCP config
            cmd = [
                self.binary, "run", message,
                "--mcp-config", self._mcp_config_json(base_url),
                "--json",
            ]
            if self._session_id:
                cmd += ["--resume", self._session_id]
            return cmd
        if self.tool == "gemini":
            # Gemini CLI — stream JSON output
            return [self.binary, "run", message, "--format", "json"]
        # Fallback: run with message as the only argument
        return [self.binary, message]

    def send(self, message, history, emit, should_stop):
        err = self.validate()
        if err:
            emit(AgentEvent(EventType.ERROR, {"error": err}))
            return history

        base_url = self._server_provider()
        cmd = self._build_command(message, base_url)
        # F15: start_new_session detaches the child from QGIS's process
        # group, so Ctrl-C in the spawned CLI doesn't kill QGIS, and
        # close_fds prevents file-descriptor leaks.
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, bufsize=1,
                    start_new_session=True, close_fds=True,
                )
        except Exception as exc:
            emit(AgentEvent(EventType.ERROR, {"error": f"Failed to launch {self.tool}: {exc}"}))
            return history

        proc = self._proc
        try:
            self._drain_process(proc, emit, should_stop)
        finally:
            self._finalize_process(proc)

        emit(AgentEvent(EventType.DONE))
        return history

    # ------------------------------------------------------------------ #
    # Streaming I/O — single-threaded select() loop, robust JSONL parser #
    # ------------------------------------------------------------------ #

    def _drain_process(self, proc, emit, should_stop):
        """Read both pipes via ``select`` and emit parsed events.

        Replacing the two reader threads eliminates a deadlock class where
        one pipe's kernel buffer (64 kB on macOS) fills before the other is
        drained — the writer blocks, the reader thread blocks on
        ``readline``, and nothing moves. ``select`` notifies us whenever
        *any* pipe is readable.
        """
        stdout = proc.stdout
        stderr = proc.stderr
        out_buf = b""
        err_acc = []
        poller_stopped = False
        # Mutable cell for the CLI's session id (captured by _emit_line).
        sid_holder = [self._session_id]

        while True:
            if should_stop():
                poller_stopped = True
                break
            if proc.poll() is not None:
                out_buf = self._read_into(out_buf, stdout, emit, sid_holder) if stdout else out_buf
                if stderr:
                    err_acc.append(self._read_all(stderr, None))
                break
            try:
                rlist, _, _ = select.select([stdout, stderr], [], [], 0.2)
            except (OSError, ValueError):
                break
            for stream in rlist:
                if stream is stdout:
                    out_buf = self._read_into(out_buf, stdout, emit, sid_holder)
                elif stream is stderr:
                    err_acc.append(self._read_all(stderr, None))

        if poller_stopped:
            try:
                proc.terminate()
            except Exception:
                pass

        # Persist the session id for the next turn's --resume.
        if sid_holder[0]:
            self._session_id = sid_holder[0]

        # If stdout was closed before we drained, pick up any leftover
        # partial line — Claude has been observed to omit a trailing
        # newline on the final record.
        if out_buf.strip():
            _emit_line(out_buf.decode("utf-8", "replace").rstrip("\r"),
                       emit, sid_holder)
            if sid_holder[0]:
                self._session_id = sid_holder[0]

        if err_acc and not poller_stopped:
            try:
                rc = proc.returncode
            except Exception:
                rc = None
            if rc not in (0, None):
                joined = "\n".join(s for s in err_acc if s).strip()
                if joined:
                    emit(AgentEvent(EventType.ERROR, {"error": joined[:2000]}))

    @staticmethod
    def _read_into(buf, stream, emit, sid_holder=None):
        """Read whatever bytes are immediately available and parse whole
        newline-terminated JSONL lines. Returns the (possibly partial)
        trailing buffer.
        """
        try:
            chunk = stream.read(4096)
        except (OSError, ValueError):
            return buf
        if not chunk:
            return buf
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            text = line.decode("utf-8", "replace").rstrip("\r")
            if text:
                _emit_line(text, emit, sid_holder)
        return buf

    @staticmethod
    def _read_all(stream, _unused):
        try:
            return stream.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def _finalize_process(self, proc):
        """Reap the child, escalating to SIGKILL if it refuses to die."""
        # F15: bounded kill. Try terminate (SIGTERM), wait 5s, then kill.
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        with self._lock:
            if self._proc is proc:
                self._proc = None

    # ------------------------------------------------------------------ #
    # JSONL event handling                                                #
    # ------------------------------------------------------------------ #


def _emit_line(line, emit, session_id_holder=None):
    """Route a single JSONL line to the appropriate AgentEvent.

    Claude emits ``assistant``/``user``/``system``/``result`` records; we
    forward assistant text and tool_use as ``TEXT`` / ``TOOL_USE``, and
    tool results as ``TOOL_RESULT``. Non-JSONL output (e.g. opencode's
    plain text fallback) is emitted as raw ``TEXT``.

    ``session_id_holder`` is a two-element list ``[value]`` used as a
    mutable cell to capture the CLI's session id for ``--resume`` on the
    next turn. Passing a list avoids a closure over the backend instance
    (this function is module-level so the backends can use it without
    owning the state).
    """
    if not line:
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        emit(AgentEvent(EventType.TEXT, {"text": line + "\n"}))
        return
    etype = event.get("type")
    sid = event.get("session_id")
    if sid and session_id_holder is not None:
        session_id_holder[0] = sid
    if etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text":
                emit(AgentEvent(EventType.TEXT, {"text": block["text"]}))
            elif block.get("type") == "tool_use":
                emit(AgentEvent(EventType.TOOL_USE,
                                {"name": block.get("name"), "input": block.get("input")}))
    elif etype == "user":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content, default=str)
                emit(AgentEvent(EventType.TOOL_RESULT, {
                    "name": "tool",
                    "result": text[:4000],
                    "is_error": bool(block.get("is_error", False)),
                }))
    elif etype in ("system", "result"):
        # Session metadata; nothing to surface to the chat.
        return
    else:
        # Unknown structured record — surface as raw text so the user
        # sees something rather than nothing.
        if event:
            emit(AgentEvent(EventType.TEXT, {"text": line + "\n"}))
