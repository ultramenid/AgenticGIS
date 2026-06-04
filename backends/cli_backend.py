"""Drive an installed, already-logged-in agent CLI.

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
import platform
import select
import shlex
import shutil
import subprocess
import threading

from .base import AgentBackend, AgentEvent, EventType


CLI_AGENT_CATALOG = (
    {
        "id": "claude",
        "label": "Claude Code",
        "commands": ("claude",),
        "credential_style": "Claude subscription or Anthropic credentials",
        "warning": "Provider policy may treat third-party automation differently.",
    },
    {
        "id": "codex",
        "label": "Codex CLI",
        "commands": ("codex",),
        "credential_style": "OpenAI API key or ChatGPT account in Codex",
    },
    {
        "id": "cursor",
        "label": "Cursor Agent",
        "commands": ("cursor-agent", "cursor"),
        "credential_style": "Cursor account or configured provider keys",
    },
    {
        "id": "gemini",
        "label": "Gemini CLI",
        "commands": ("gemini",),
        "credential_style": "Google account or Gemini API key",
    },
    {
        "id": "copilot",
        "label": "GitHub Copilot CLI",
        "commands": ("gh", "copilot"),
        "credential_style": "GitHub Copilot subscription",
    },
    {
        "id": "opencode",
        "label": "OpenCode",
        "commands": ("opencode",),
        "credential_style": "Provider keys in OpenCode config",
    },
    {
        "id": "qwen",
        "label": "Qwen Code",
        "commands": ("qwen",),
        "credential_style": "DashScope or Qwen API key",
    },
    {
        "id": "grok",
        "label": "Grok",
        "commands": ("grok",),
        "credential_style": "xAI account or key",
    },
    {
        "id": "hermes",
        "label": "Hermes",
        "commands": ("hermes",),
        "credential_style": "Configured provider keys",
    },
    {
        "id": "kimi",
        "label": "Kimi CLI",
        "commands": ("kimi",),
        "credential_style": "Moonshot/Kimi API key",
    },
    {
        "id": "devin",
        "label": "Devin for Terminal",
        "commands": ("devin",),
        "credential_style": "Devin account",
    },
    {
        "id": "deepseek_tui",
        "label": "DeepSeek TUI",
        "commands": ("deepseek", "deepseek-tui"),
        "credential_style": "DeepSeek API key",
    },
    {
        "id": "pi",
        "label": "Pi",
        "commands": ("pi",),
        "credential_style": "Pi account",
    },
    {
        "id": "mistral_vibe",
        "label": "Mistral Vibe CLI",
        "commands": ("mistral-vibe", "vibe"),
        "credential_style": "Mistral API key",
    },
    {
        "id": "kiro",
        "label": "Kiro CLI",
        "commands": ("kiro",),
        "credential_style": "AWS credentials",
    },
    {
        "id": "kilo",
        "label": "Kilo",
        "commands": ("kilo",),
        "credential_style": "Configured provider keys",
    },
    {
        "id": "qoder",
        "label": "Qoder CLI",
        "commands": ("qoder",),
        "credential_style": "Qoder account or provider keys",
    },
)

_AGENT_BY_ID = {agent["id"]: agent for agent in CLI_AGENT_CATALOG}


def agent_by_id(agent_id):
    """Return a CLI agent catalog entry by id, or None."""
    return _AGENT_BY_ID.get(agent_id)


def _is_usable_file(path):
    return bool(path and os.path.exists(path) and os.access(path, os.X_OK))


def _looks_like_agent_binary(path):
    """Filter out launcher stubs that exist but immediately report missing tools."""
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=4,
        )
    except Exception:
        return True
    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if result.returncode == 127 and "not found in path" in output:
        return False
    return True


def _unique_paths(paths):
    seen = set()
    for path in paths:
        if not path:
            continue
        expanded = os.path.expanduser(os.path.expandvars(path))
        key = os.path.normcase(os.path.abspath(expanded))
        if key in seen:
            continue
        seen.add(key)
        yield expanded


def _windows_program_dirs():
    yielded = False
    for key in ("LOCALAPPDATA", "APPDATA", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        value = os.environ.get(key)
        if value:
            yielded = True
            yield value
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        yielded = True
        yield os.path.join(userprofile, ".local", "bin")
    if not yielded:
        yield "~/.local/bin"
        yield "~/AppData/Roaming/npm"
        yield "~/AppData/Local/Programs"


def _command_file_names(command, system=None):
    system = system or platform.system()
    if system == "Windows":
        if command.lower().endswith((".exe", ".cmd", ".bat")):
            return (command,)
        return (f"{command}.exe", f"{command}.cmd", f"{command}.bat", command)
    return (command,)


def _candidate_paths(tool, command, system=None):
    """Known install locations when QGIS launches without the user's shell PATH."""
    system = system or platform.system()
    names = _command_file_names(command, system)
    paths = []

    if system == "Windows":
        for root in _windows_program_dirs():
            for name in names:
                paths.extend([
                    os.path.join(root, name),
                    os.path.join(root, "npm", name),
                    os.path.join(root, "bin", name),
                    os.path.join(root, "Programs", name),
                    os.path.join(root, "Programs", tool, name),
                    os.path.join(root, tool, name),
                    os.path.join(root, "Claude", name),
                    os.path.join(root, "ClaudeCode", name),
                    os.path.join(root, "Anthropic", "Claude Code", name),
                ])
        return list(_unique_paths(paths))

    home_roots = [
        "~/.local/bin",
        f"~/.{tool}/bin",
        f"~/.{tool}/local",
        "~/.npm-global/bin",
        "~/node_modules/.bin",
    ]
    package_roots = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/snap/bin",
    ]
    if system == "Darwin":
        package_roots.extend([
            "/Applications/Claude.app/Contents/MacOS",
            "/Applications/Claude Code.app/Contents/MacOS",
        ])

    for root in home_roots + package_roots:
        for name in names:
            paths.append(os.path.join(root, name))

    if tool == "claude":
        for name in names:
            paths.extend([
                os.path.join("~/.claude/local", name),
                os.path.join("~/.local/share/claude", name),
            ])
    elif tool == "opencode":
        for name in names:
            paths.append(os.path.join("~/.opencode/bin", name))
    elif tool == "gemini":
        for name in names:
            paths.append(os.path.join("~/.gemini/bin", name))
    elif tool == "codex":
        for name in names:
            paths.append(os.path.join("~/.codex/bin", name))
    elif tool == "pi":
        for name in names:
            paths.extend([
                os.path.join("~/.pi/bin", name),
                os.path.join("~/.local/share/pi", name),
                os.path.join("/opt/homebrew/bin", name),
                os.path.join("/opt/homebrew/Cellar/node", "*", "bin", name),
            ])

    return list(_unique_paths(paths))


def _resolve_binary(tool, explicit_path):
    if explicit_path and _is_usable_file(explicit_path):
        return explicit_path
    agent = agent_by_id(tool)
    command_names = agent.get("commands", (tool,)) if agent else (tool,)
    for command in command_names:
        found = shutil.which(command)
        if found and _looks_like_agent_binary(found):
            return found
        for candidate in _candidate_paths(tool, command):
            if "*" in candidate:
                import glob
                expanded = sorted(glob.glob(candidate), reverse=True)
            else:
                expanded = [candidate]
            for path in expanded:
                if _is_usable_file(path) and _looks_like_agent_binary(path):
                    return path
    return None


def scan_cli_agents(path_overrides=None):
    """Return catalog rows with detected path/install status."""
    path_overrides = path_overrides or {}
    rows = []
    for index, agent in enumerate(CLI_AGENT_CATALOG):
        row = dict(agent)
        path = _resolve_binary(agent["id"], path_overrides.get(agent["id"], ""))
        row["path"] = path or ""
        row["real_path"] = os.path.realpath(path) if path else ""
        row["installed"] = bool(path)
        row["_catalog_index"] = index
        rows.append(row)
    rows.sort(key=lambda row: (not row["installed"], row["_catalog_index"]))
    return rows


class CliToolBackend(AgentBackend):
    def __init__(self, config, server_provider):
        self.config = config
        self.tool = config.get("cli_tool")
        self.binary = _resolve_binary(self.tool, config.get("cli_path"))
        try:
            self.extra_args = shlex.split(config.get("cli_args") or "")
        except ValueError:
            self.extra_args = []
        # Zero-arg callable returning the running MCP server's base URL.
        self._server_provider = server_provider
        # synchronise mutations of _proc and _session_id across the
        # dock and the worker thread.
        self._lock = threading.Lock()
        self._proc = None
        self._session_id = None

    @property
    def label(self):
        agent = agent_by_id(self.tool)
        label = agent["label"] if agent else self.tool
        return f"CLI Agent ({label})"

    def validate(self):
        if self.binary is None:
            return (f"Could not find the '{self.tool}' binary. Set its path in "
                    "Settings, or make sure it is on PATH.")
        if self._server_provider is None or self._server_provider() is None:
            return "The local MCP bridge could not start."
        return None

    def export_session_state(self):
        with self._lock:
            return {"session_id": self._session_id} if self._session_id else {}

    def import_session_state(self, state):
        with self._lock:
            self._session_id = (state or {}).get("session_id") or None

    # ------------------------------------------------------------------ #
    # Login / auth helpers
    # ------------------------------------------------------------------ #

    def _check_login_cmd(self):
        """Return the command to check authentication status."""
        if self.tool == "claude":
            return [self.binary, "auth", "status"]
        if self.tool == "codex":
            return [self.binary, "login", "status"]
        if self.tool in ("opencode", "gemini"):
            return [self.binary, "status"]
        return None

    def _env_auth_status(self):
        """Cheap readiness checks for CLIs that rely on provider env vars."""
        keys_by_tool = {
            "pi": (
                "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "OPENAI_API_KEY",
                "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
                "OPENROUTER_API_KEY", "KIMI_API_KEY", "MISTRAL_API_KEY",
            ),
        }
        keys = keys_by_tool.get(self.tool, ())
        configured = [key for key in keys if os.environ.get(key)]
        if configured:
            return "ready", f"Env key configured: {configured[0]}"
        if keys:
            return "unsupported", "Auth check unavailable. Run the CLI directly to verify login or API keys."
        return None

    def auth_status(self):
        """Return (state, detail) for the selected CLI's auth readiness.

        ``state`` is one of: ``ready``, ``login_required``, ``unsupported``,
        or ``missing``.
        """
        if not self.binary:
            return "missing", "Binary not found"
        env_status = self._env_auth_status()
        if env_status:
            return env_status
        cmd = self._check_login_cmd()
        if not cmd:
            return "unsupported", "Auth check unavailable for this CLI."
        try:
            result = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=8,
            )
        except Exception as exc:  # noqa: BLE001
            return "unsupported", str(exc)

        output = (result.stdout or result.stderr or "").strip()
        detail = output.splitlines()[0] if output else ""
        if self.tool == "claude" and output.startswith("{"):
            try:
                payload = json.loads(output)
                if payload.get("loggedIn") is True:
                    auth_method = payload.get("authMethod") or "logged in"
                    provider = payload.get("apiProvider") or ""
                    detail = " · ".join(part for part in (auth_method, provider) if part)
                elif payload.get("loggedIn") is False:
                    detail = "Not logged in"
            except Exception:
                pass
        if result.returncode == 0:
            return "ready", detail or "Logged in"
        if result.returncode in (1, 2) and detail:
            return "login_required", detail
        return "unsupported", detail or "No auth status command available"

    def check_login(self):
        """Return True if the CLI reports an active session."""
        return self.auth_status()[0] == "ready"

    def _login_cmd(self):
        """Return the command to open a browser login flow."""
        if self.tool == "claude":
            return [self.binary, "auth", "login"]
        if self.tool in ("opencode", "codex", "gemini"):
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

    def test_cli(self):
        """Run a lightweight smoke check for settings UI diagnostics."""
        if not self.binary:
            return False, "Binary not found"
        commands = [
            [self.binary, "--version"],
            [self.binary, "version"],
            [self.binary, "--help"],
        ]
        if self.tool == "copilot" and os.path.basename(self.binary) == "gh":
            commands.insert(0, [self.binary, "copilot", "--help"])
        last_err = ""
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=8,
                )
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue
            output = (result.stdout or result.stderr or "").strip()
            if result.returncode == 0:
                return True, (output.splitlines()[0] if output else "OK")
            if output:
                last_err = output.splitlines()[0]
        return False, last_err or "CLI did not respond successfully"

    # ------------------------------------------------------------------ #
    def _mcp_config_json(self, base_url):
        return json.dumps({
            "mcpServers": {
                "AgenticGIS": {"type": "http", "url": base_url}
            }
        })

    def _with_guardrails(self, message):
        return (
            "You are operating AgenticGIS inside QGIS. Stay within AgenticGIS "
            "scope: QGIS, loaded project layers, spatial data, GIS analysis, "
            "maps, and plugin/QGIS automation. If the user asks for something "
            "outside that context or outside this plugin's capability, respond "
            "exactly: we dont do that here\n\n"
            "Access outside currently loaded project layers (external files, "
            "folders, URLs, databases, or filesystem/network reads/writes) "
            "requires explicit user permission. If permission is denied, do "
            "not work around it.\n\n"
            "Prefer analyze_layer before arbitrary run_pyqgis for layer "
            "analysis. For large layers, use structured summaries, statistics, "
            "charts, sampling, or bounded iteration. Do not use "
            "list(layer.getFeatures()), do not materialize all features. Do "
            "not fetch geometry when only attributes are needed.\n\n"
            "If the user explicitly asks to remove or clear loaded layers, "
            "use remove_layer or clear_layers. These tools unload layers from "
            "the QGIS project only; they never delete source files.\n\n"
            "When you add a derived result layer, call add_layer with "
            "is_analysis=true so it is kept as a persistent result and reused "
            "by name. Do not force canvas zoom/refresh on large layer loads; "
            "call zoom_to_layer(layer_id) only when the user asks to inspect "
            "the result immediately. Do not delete analysis layers.\n\n"
            "For remote sensing, satellite imagery, Earth Engine/GEE, NDVI, or "
            "spectral indices: call gee_status first to verify the GEE plugin "
            "is installed and authenticated, then ask the user to confirm "
            "before running gee_add_layer.\n\n"
            f"User request: {message}"
        )

    def _build_command(self, message, base_url):
        message = self._with_guardrails(message)
        if self.tool == "claude":
            cmd = [
                self.binary, "-p", message,
                "--output-format", "stream-json", "--verbose",
                "--mcp-config", self._mcp_config_json(base_url),
                "--permission-mode", "bypassPermissions",
            ]
            if self._session_id:
                cmd += ["--resume", self._session_id]
            return cmd + self.extra_args
        if self.tool == "opencode":
            return [self.binary, "run", message] + self.extra_args
        if self.tool == "codex":
            # Codex CLI (beta) — headless mode with MCP config
            cmd = [
                self.binary, "run", message,
                "--mcp-config", self._mcp_config_json(base_url),
                "--json",
            ]
            if self._session_id:
                cmd += ["--resume", self._session_id]
            return cmd + self.extra_args
        if self.tool == "gemini":
            # Gemini CLI — stream JSON output
            return [self.binary, "run", message, "--format", "json"] + self.extra_args
        # Fallback: run with message as the only argument
        return [self.binary, message] + self.extra_args

    def send(self, message, history, emit, should_stop):
        err = self.validate()
        if err:
            emit(AgentEvent(EventType.ERROR, {"error": err}))
            return history

        base_url = self._server_provider()
        cmd = self._build_command(message, base_url)
        # start_new_session detaches the child from QGIS's process
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

    @staticmethod
    def _close_process_pipes(proc):
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass

    def _finalize_process(self, proc):
        """Reap the child, escalating to SIGKILL if it refuses to die."""
        # bounded kill. Try terminate (SIGTERM), wait 5s, then kill.
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
        self._close_process_pipes(proc)
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
