"""Use an installed, already-logged-in agent CLI as a model proxy.

This backend no longer exposes QGIS through MCP to the spawned CLI. Instead it
prompts the CLI for a small JSON instruction, then AgenticGIS executes any QGIS
tool calls in-process just like the custom/API backends. Because the CLI is
already authenticated, no API key is needed and AgenticGIS never reads the
CLI-owned OAuth tokens.

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
import signal
import subprocess
import tempfile
import threading

from .base import AgentBackend, AgentEvent, EventType, _dispatch_one_tool
from ..core import tools as tools_mod
from .base import agent_iteration_steps
from .openai_backend import DEFAULT_SYSTEM_PROMPT as AGENTICGIS_SYSTEM_PROMPT


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
    if not path or not os.path.isfile(path):
        return False
    if platform.system() == "Windows":
        return os.path.splitext(path)[1].lower() in (".exe", ".cmd", ".bat", ".com")
    return os.access(path, os.X_OK)


def _subprocess_cmd(cmd):
    """Wrap .cmd/.bat files with ``cmd.exe /c`` on Windows.

    On Windows only .exe files are directly executable by the OS loader.
    npm-global and Scoop shims are .cmd files; without this wrapper
    subprocess raises WinError 193 / WinError 2.
    """
    if not cmd or platform.system() != "Windows":
        return list(cmd)
    binary = cmd[0]
    if isinstance(binary, str) and binary.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c"] + list(cmd)
    return list(cmd)


def _looks_like_agent_binary(path):
    """Filter out launcher stubs that exist but immediately report missing tools."""
    try:
        result = subprocess.run(
            _subprocess_cmd([path, "--version"]), capture_output=True, text=True, timeout=4,
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


def _empty_runtime_dir(name):
    path = os.path.join(tempfile.gettempdir(), "AgenticGIS", name)
    os.makedirs(path, exist_ok=True)
    return path


def _opencode_config_json():
    return json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "instructions": [],
        "plugin": [],
        "skills": {"paths": [], "urls": []},
        "mcp": {},
    })


def _devin_config_json():
    return json.dumps({
        "permissions": {"allow": [], "deny": [], "ask": []},
        "mcpServers": {},
        "read_config_from": {
            "cursor": False,
            "windsurf": False,
            "claude": False,
        },
    })


def _runtime_json_file(name, content):
    path = os.path.join(_empty_runtime_dir(name), "config.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _build_default_command(backend, prompt):
    return [backend.binary, prompt] + backend.extra_args


def _build_claude_command(backend, prompt):
    return [
        backend.binary, "-p", prompt,
        *backend.extra_args,
        "--output-format", "stream-json", "--verbose",
        "--setting-sources", "local",
        "--settings", "{}",
        "--disable-slash-commands",
        "--plugin-dir", _empty_runtime_dir("claude-empty-plugins"),
        "--no-session-persistence",
    ]


def _build_opencode_command(backend, prompt):
    return [
        backend.binary, "run", prompt,
        *backend.extra_args,
        "--pure",
        "--format", "json",
    ]


def _build_codex_command(backend, prompt):
    return [
        backend.binary, "exec",
        *backend.extra_args,
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--disable", "apps",
        "--disable", "plugins",
        "--cd", _empty_runtime_dir("codex-empty-workspace"),
        "--json",
        prompt,
    ]


def _build_cursor_command(backend, prompt):
    base = os.path.basename(backend.binary or "")
    if base.startswith("cursor") and not base.startswith("cursor-agent"):
        return [
            backend.binary, "agent", "-p", prompt,
            *backend.extra_args,
            "--output-format", "json",
        ]
    return [
        backend.binary, "-p", prompt,
        *backend.extra_args,
        "--output-format", "json",
    ]


def _build_gemini_command(backend, prompt):
    return [
        backend.binary, "-p", prompt,
        *backend.extra_args,
        "--output-format", "json",
        "--approval-mode", "default",
        "--extensions", "none",
    ]


def _build_qwen_command(backend, prompt):
    return [
        backend.binary, "--prompt", prompt,
        *backend.extra_args,
        "--output-format", "stream-json",
    ]


def _build_devin_command(backend, prompt):
    return [
        backend.binary, "--print",
        "--config", _runtime_json_file("devin-config", _devin_config_json()),
        *backend.extra_args,
        "--", prompt,
    ]


def _build_kiro_command(backend, prompt):
    return [
        backend.binary, "chat",
        "--no-interactive",
        *backend.extra_args,
        prompt,
    ]


def _build_pi_command(backend, prompt):
    return [
        backend.binary,
        "--print", prompt,
        *backend.extra_args,
        "--mode", "json",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-session",
        "--offline",
    ]


def _build_copilot_command(backend, prompt):
    if os.path.basename(backend.binary or "") == "gh":
        return [
            backend.binary, "copilot", "suggest",
            *backend.extra_args,
            prompt,
        ]
    return [
        backend.binary, "suggest",
        *backend.extra_args,
        prompt,
    ]


def _copilot_test_commands(backend):
    if os.path.basename(backend.binary or "") == "gh":
        return [[backend.binary, "copilot", "--help"]]
    return []


def _opencode_env():
    opencode_config_path = _runtime_json_file("opencode-config", _opencode_config_json())
    return {
        "OPENCODE_CONFIG_CONTENT": _opencode_config_json(),
        "OPENCODE_CONFIG": opencode_config_path,
        "OPENCODE_CONFIG_DIR": os.path.dirname(opencode_config_path),
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_PURE": "1",
        "OPENCODE_ENABLE_EXA": "0",
    }


def _parse_claude_auth_detail(output, default_detail):
    if not output.startswith("{"):
        return default_detail
    try:
        payload = json.loads(output)
    except Exception:
        return default_detail
    if payload.get("loggedIn") is True:
        auth_method = payload.get("authMethod") or "logged in"
        provider = payload.get("apiProvider") or ""
        return " · ".join(part for part in (auth_method, provider) if part)
    if payload.get("loggedIn") is False:
        return "Not logged in"
    return default_detail


_COMMON_RUNTIME_ENV = {
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "PI_OFFLINE": "1",
}


_RUNTIME_PATH_HINTS = (
    # Tool-specific well-known bin dirs
    "~/.local/bin",
    "~/.opencode/bin",
    "~/.codex/bin",
    "~/.gemini/bin",
    "~/.claude/local",
    "~/.kimi-code/bin",
    "~/.kiro/bin",
    "~/node_modules/.bin",
    # System package managers
    "/opt/homebrew/bin",
    "/opt/homebrew/Cellar/node/*/bin",
    "/opt/local/bin",        # MacPorts
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/snap/bin",
)


_ENV_AUTH_KEYS = {
    "pi": (
        "ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "OPENAI_API_KEY",
        "GEMINI_API_KEY", "DEEPSEEK_API_KEY", "XAI_API_KEY",
        "OPENROUTER_API_KEY", "KIMI_API_KEY", "MISTRAL_API_KEY",
    ),
}


_EXTRA_CANDIDATE_ROOTS = {
    "claude": ("~/.claude/local", "~/.local/share/claude"),
    "opencode": ("~/.opencode/bin",),
    "gemini": ("~/.gemini/bin",),
    "codex": ("~/.codex/bin",),
    "kimi": ("~/.kimi-code/bin", "~/.kimi/bin"),
    "kiro": ("~/.kiro/bin",),
    "pi": (
        "~/.pi/bin",
        "~/.local/share/pi",
        "/opt/homebrew/bin",
        "/opt/homebrew/Cellar/node/*/bin",
    ),
}


_CLI_AGENT_PROFILES = {
    "claude": {
        "auth_status_args": ("auth", "status"),
        "login_args": ("auth", "login"),
        "build_command": _build_claude_command,
        "parse_auth_detail": _parse_claude_auth_detail,
    },
    "codex": {
        "auth_status_args": ("login", "status"),
        "login_args": ("login",),
        "build_command": _build_codex_command,
    },
    "cursor": {"build_command": _build_cursor_command},
    "gemini": {
        "auth_status_args": ("status",),
        "login_args": ("login",),
        "build_command": _build_gemini_command,
    },
    "opencode": {
        "auth_status_args": ("status",),
        "login_args": ("login",),
        "build_command": _build_opencode_command,
        "env": _opencode_env,
    },
    "qwen": {"build_command": _build_qwen_command},
    "devin": {"build_command": _build_devin_command},
    "kiro": {"build_command": _build_kiro_command},
    "pi": {"build_command": _build_pi_command},
    "copilot": {
        "build_command": _build_copilot_command,
        "test_commands": _copilot_test_commands,
    },
}


def _agent_profile(tool):
    return _CLI_AGENT_PROFILES.get(tool or "", {})


def _existing_dirs(paths):
    out = []
    for path in paths:
        if not path:
            continue
        expanded = os.path.expanduser(os.path.expandvars(path))
        if "*" in expanded:
            import glob
            candidates = glob.glob(expanded)
        else:
            candidates = [expanded]
        for candidate in candidates:
            if os.path.isdir(candidate):
                out.append(candidate)
    return out


def _prepend_path(env, paths):
    path_key = next((key for key in env if key.lower() == "path"), "PATH")
    existing = env.get(path_key, "")
    seen = set()
    merged = []
    for entry in list(paths) + [part for part in existing.split(os.pathsep) if part]:
        norm = os.path.normcase(os.path.abspath(os.path.expanduser(entry)))
        if norm in seen:
            continue
        seen.add(norm)
        merged.append(entry)
    env[path_key] = os.pathsep.join(merged)


def _scrub_child_env(env):
    exact = {
        "CODEX_CI",
        "CODEX_SANDBOX",
        "CODEX_SANDBOX_NETWORK_DISABLED",
        "CODEX_THREAD_ID",
        "CLAUDE_CODE_SSE_PORT",
        "CLAUDE_CODE_ENTRYPOINT",
    }
    prefixes = (
        "MCP__PLUGIN_",
        "SUPERPOWERS_",
    )
    for key in list(env):
        upper = key.upper()
        if upper in exact or any(upper.startswith(prefix) for prefix in prefixes):
            env.pop(key, None)


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


def _windows_package_manager_bin_dirs():
    """Yield known package-manager binary/shim directories on Windows.

    These are directories where executables land directly (no further
    sub-path joining needed).  Checked in order: env-var-customised paths
    first, then well-known defaults.
    """
    localappdata = os.environ.get("LOCALAPPDATA") or ""
    appdata = os.environ.get("APPDATA") or ""
    userprofile = os.environ.get("USERPROFILE") or ""
    programfiles = os.environ.get("ProgramFiles") or ""
    programdata = os.environ.get("ProgramData") or ""

    # npm global: on Windows executables land directly in the prefix dir.
    # Default prefix is %APPDATA%\npm (NOT %APPDATA%\npm\bin as on Unix).
    if appdata:
        yield os.path.join(appdata, "npm")

    # Scoop shims (per-user install)
    scoop_root = os.environ.get("SCOOP") or (
        os.path.join(userprofile, "scoop") if userprofile else ""
    )
    if scoop_root:
        yield os.path.join(scoop_root, "shims")

    # Scoop shims (system-wide install)
    scoop_global = os.environ.get("SCOOP_GLOBAL") or (
        os.path.join(programdata, "scoop") if programdata else ""
    )
    if scoop_global:
        yield os.path.join(scoop_global, "shims")

    # Volta shims: %VOLTA_HOME%\bin or %LOCALAPPDATA%\Volta\bin
    volta_home = os.environ.get("VOLTA_HOME") or (
        os.path.join(localappdata, "Volta") if localappdata else ""
    )
    if volta_home:
        yield os.path.join(volta_home, "bin")
    # Volta system installer puts binaries directly in %ProgramFiles%\Volta
    if programfiles:
        yield os.path.join(programfiles, "Volta")

    # Chocolatey: %ChocolateyInstall%\bin
    choco = os.environ.get("ChocolateyInstall") or (
        os.path.join(programdata, "chocolatey") if programdata else ""
    )
    if choco:
        yield os.path.join(choco, "bin")

    # pnpm global: %PNPM_HOME% or %LOCALAPPDATA%\pnpm
    pnpm_home = os.environ.get("PNPM_HOME") or (
        os.path.join(localappdata, "pnpm") if localappdata else ""
    )
    if pnpm_home:
        yield pnpm_home

    # nvm-windows: NVM_SYMLINK points to the currently active Node.js dir
    nvm_symlink = os.environ.get("NVM_SYMLINK")
    if nvm_symlink:
        yield nvm_symlink
    # NVM_HOME contains per-version subdirs; the active symlink is better but
    # fall back to probing version dirs via glob in _candidate_paths.
    nvm_home = os.environ.get("NVM_HOME")
    if nvm_home:
        yield nvm_home

    # Yarn global: %LOCALAPPDATA%\Yarn\bin
    if localappdata:
        yield os.path.join(localappdata, "Yarn", "bin")

    # winget managed symlinks: %LOCALAPPDATA%\Microsoft\WinGet\Links
    if localappdata:
        yield os.path.join(localappdata, "Microsoft", "WinGet", "Links")


def _unix_package_manager_bin_dirs():
    """Yield known package-manager binary directories on macOS and Linux.

    Called when QGIS launches without the user's full shell PATH.
    Respects env-var overrides before falling back to well-known defaults.
    Glob patterns (``*``) are left in place — callers that pass results
    through ``_existing_dirs`` or ``_resolve_binary`` expand them correctly.
    """
    # Volta: $VOLTA_HOME/bin or ~/.volta/bin
    volta_home = os.environ.get("VOLTA_HOME") or "~/.volta"
    yield os.path.join(volta_home, "bin")

    # nvm: newest installed Node version first when glob-sorted
    nvm_dir = os.environ.get("NVM_DIR") or "~/.nvm"
    yield os.path.join(nvm_dir, "versions", "node", "*", "bin")

    # fnm (Fast Node Manager): env var or OS-specific defaults
    fnm_dir = os.environ.get("FNM_DIR")
    if fnm_dir:
        yield os.path.join(fnm_dir, "node-versions", "*", "installation", "bin")
    else:
        yield "~/.local/share/fnm/node-versions/*/installation/bin"
        yield "~/Library/Application Support/fnm/node-versions/*/installation/bin"

    # asdf version manager shims: $ASDF_DATA_DIR/shims or ~/.asdf/shims
    asdf_data = os.environ.get("ASDF_DATA_DIR") or "~/.asdf"
    yield os.path.join(asdf_data, "shims")

    # mise / rtx shims: $MISE_DATA_DIR/shims or ~/.local/share/mise/shims
    mise_data = os.environ.get("MISE_DATA_DIR") or "~/.local/share/mise"
    yield os.path.join(mise_data, "shims")
    yield "~/.mise/shims"

    # pnpm global bin: $PNPM_HOME or ~/.local/share/pnpm
    pnpm_home = os.environ.get("PNPM_HOME") or "~/.local/share/pnpm"
    yield pnpm_home

    # Yarn global bin: $YARN_GLOBAL_FOLDER/bin or ~/.yarn/bin
    yarn_global = os.environ.get("YARN_GLOBAL_FOLDER")
    yield os.path.join(yarn_global, "bin") if yarn_global else "~/.yarn/bin"

    # MacPorts
    yield "/opt/local/bin"


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
        # 1. Package-manager shim/bin dirs — executables live here directly.
        for bin_dir in _windows_package_manager_bin_dirs():
            for name in names:
                paths.append(os.path.join(bin_dir, name))

        # 2. nvm-windows version dirs via glob (%NVM_HOME%\v*\)
        nvm_home = os.environ.get("NVM_HOME")
        if nvm_home:
            import glob as _glob
            for ver_dir in sorted(_glob.glob(os.path.join(nvm_home, "v*")), reverse=True):
                for name in names:
                    paths.append(os.path.join(ver_dir, name))

        # 3. Broader program-root dirs with common sub-path patterns.
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

    # 1. Package-manager shim/bin dirs (Volta, nvm, fnm, asdf, mise, pnpm, Yarn…)
    for bin_dir in _unix_package_manager_bin_dirs():
        for name in names:
            paths.append(os.path.join(bin_dir, name))

    # 2. Common user-local and npm-global dirs
    home_roots = [
        "~/.local/bin",
        f"~/.{tool}/bin",
        f"~/.{tool}/local",
        "~/.npm-global/bin",
        "~/node_modules/.bin",
    ]

    # 3. System-wide package manager prefixes
    package_roots = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/snap/bin",
        "/opt/local/bin",    # MacPorts
    ]
    if system == "Darwin":
        package_roots.extend([
            "/opt/homebrew/Cellar/node/*/bin",
            "/Applications/Claude.app/Contents/MacOS",
            "/Applications/Claude Code.app/Contents/MacOS",
        ])

    for root in home_roots + package_roots:
        for name in names:
            paths.append(os.path.join(root, name))

    # 4. Per-tool known install roots
    for root in _EXTRA_CANDIDATE_ROOTS.get(tool, ()):
        for name in names:
            paths.append(os.path.join(root, name))

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
    def __init__(self, config, toolkit=None, executor=None, server_provider=None):
        self.config = config
        self.tool = config.get("cli_tool")
        self.binary = _resolve_binary(self.tool, config.get("cli_path"))
        try:
            self.extra_args = shlex.split(config.get("cli_args") or "")
        except ValueError:
            self.extra_args = []
        self.toolkit = toolkit
        self.executor = executor
        # Kept only for backwards-compatible construction; no longer used for
        # CLI chat because CLI mode is an in-process proxy, not an MCP client.
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
        return None

    def export_session_state(self):
        with self._lock:
            return {"session_id": self._session_id} if self._session_id else {}

    def import_session_state(self, state):
        with self._lock:
            self._session_id = (state or {}).get("session_id") or None

    def cancel_current_request(self):
        """Terminate the active CLI process group immediately."""
        with self._lock:
            proc = self._proc
        if proc is None:
            return
        self._terminate_process_group(proc, kill=True)

    # ------------------------------------------------------------------ #
    # Login / auth helpers
    # ------------------------------------------------------------------ #

    def _check_login_cmd(self):
        """Return the command to check authentication status."""
        args = _agent_profile(self.tool).get("auth_status_args")
        return [self.binary, *args] if args else None

    def _env_auth_status(self):
        """Cheap readiness checks for CLIs that rely on provider env vars."""
        keys = _ENV_AUTH_KEYS.get(self.tool, ())
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
                _subprocess_cmd(cmd),
                capture_output=True, text=True, timeout=8,
            )
        except Exception as exc:  # noqa: BLE001
            return "unsupported", str(exc)

        output = (result.stdout or result.stderr or "").strip()
        detail = output.splitlines()[0] if output else ""
        parser = _agent_profile(self.tool).get("parse_auth_detail")
        if callable(parser):
            detail = parser(output, detail)
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
        args = _agent_profile(self.tool).get("login_args", ("login",))
        return [self.binary, *args]

    def login_browser(self):
        """Launch the browser-based login flow."""
        if not self.binary:
            return False
        try:
            subprocess.Popen(
                _subprocess_cmd(self._login_cmd()),
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
        test_commands = _agent_profile(self.tool).get("test_commands")
        if callable(test_commands):
            commands = list(test_commands(self)) + commands
        last_err = ""
        for cmd in commands:
            try:
                result = subprocess.run(
                    _subprocess_cmd(cmd), capture_output=True, text=True, timeout=8,
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
    def _system_prompt(self):
        return (
            f"{AGENTICGIS_SYSTEM_PROMPT}\n\n"
            "## CLI proxy rules\n\n"
            "You are running inside a CLI transport, but AgenticGIS executes "
            "all QGIS tools in-process. Do not use this CLI's own filesystem, "
            "shell, browser, plugin, skill, or MCP tools to answer the user's "
            "GIS request. When you need project data, request an AgenticGIS "
            "tool call in the JSON protocol below.\n\n"
            "For final answers, write normal plain text/markdown directly, "
            "with the same rich, useful, detailed answer you would give in "
            "API/custom mode. Do not wrap final answers in JSON. Preserve the "
            "normal AgenticGIS style: concrete findings, useful context, "
            "tables/charts/layers when relevant, and one useful follow-up "
            "sentence after analysis.\n\n"
            "Return JSON ONLY when you need to call one or more AgenticGIS "
            "tools. No markdown fences, no extra text around the JSON.\n"
            "To call one or more AgenticGIS tools:\n"
            "{\"type\":\"tool_calls\",\"calls\":[{\"name\":\"list_layers\",\"arguments\":{}}]}\n"
            "Use tool_calls when QGIS project data is needed. Use plain final "
            "text for ordinary conversation or once tool results are sufficient."
        )

    def _tool_prompt(self):
        specs = [
            {
                "name": spec["name"],
                "description": spec["description"],
                "input_schema": spec["input_schema"],
            }
            for spec in tools_mod.TOOL_SPECS
        ]
        return json.dumps(specs, default=str)

    def _conversation_prompt(self, messages):
        return (
            f"{self._system_prompt()}\n\n"
            f"Available AgenticGIS tools:\n{self._tool_prompt()}\n\n"
            "Conversation, newest last:\n"
            f"{json.dumps(messages, default=str)}"
        )

    def _build_command(self, prompt, _base_url=None):
        builder = _agent_profile(self.tool).get("build_command", _build_default_command)
        return builder(self, prompt)

    def _runtime_env(self):
        env = os.environ.copy()
        _scrub_child_env(env)
        if platform.system() == "Windows":
            extra = list(_windows_package_manager_bin_dirs())
        else:
            extra = list(_unix_package_manager_bin_dirs())
        node_and_wrapper_dirs = _existing_dirs(
            [os.path.dirname(self.binary or ""), *extra, *_RUNTIME_PATH_HINTS]
        )
        _prepend_path(env, node_and_wrapper_dirs)
        env.update(_COMMON_RUNTIME_ENV)
        profile_env = _agent_profile(self.tool).get("env")
        if callable(profile_env):
            env.update(profile_env())
        elif isinstance(profile_env, dict):
            env.update(profile_env)
        return env

    def _runtime_cwd(self):
        return _empty_runtime_dir(f"{self.tool or 'cli'}-workspace")

    def _collect_process_output(self, cmd, env, cwd, should_stop):
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    _subprocess_cmd(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0,
                    env=env,
                    cwd=cwd,
                    start_new_session=True, close_fds=True,
                )
        except Exception as exc:
            return "", f"Failed to launch {self.tool}: {exc}", False

        proc = self._proc
        stdout = proc.stdout
        stderr = proc.stderr
        out_chunks = []
        err_chunks = []
        stopped = False
        try:
            while True:
                if should_stop():
                    stopped = True
                    self._terminate_process_group(proc, kill=True)
                    break
                if proc.poll() is not None:
                    for stream, chunks in ((stdout, out_chunks), (stderr, err_chunks)):
                        if not stream:
                            continue
                        while True:
                            try:
                                rest = os.read(stream.fileno(), 4096)
                            except Exception:
                                rest = b""
                            if not rest:
                                break
                            chunks.append(rest)
                    break
                try:
                    readable, _, _ = select.select([stdout, stderr], [], [], 0.2)
                except (OSError, ValueError):
                    continue
                for stream in readable:
                    try:
                        chunk = os.read(stream.fileno(), 4096)
                    except Exception:
                        chunk = b""
                    if not chunk:
                        continue
                    if stream is stdout:
                        out_chunks.append(chunk)
                    else:
                        err_chunks.append(chunk)
        finally:
            self._finalize_process(proc, stopped=stopped)

        out_text = b"".join(out_chunks).decode("utf-8", "replace")
        err_text = b"".join(err_chunks).decode("utf-8", "replace")
        if stopped:
            return out_text, "Stopped.", False
        return out_text, err_text, proc.returncode == 0

    def _run_model_proxy(self, messages, should_stop):
        prompt = self._conversation_prompt(messages)
        cmd = self._build_command(prompt, None)
        out_text, err_text, ok = self._collect_process_output(
            cmd, self._runtime_env(), self._runtime_cwd(), should_stop
        )
        if not ok and err_text.strip():
            return "", err_text.strip()
        return self._extract_model_text(out_text), ""

    def _extract_model_text(self, output):
        """Extract assistant text from common CLI JSONL/event wrappers."""
        if not output:
            return ""
        final_texts = []
        texts = []
        raw_non_json = []
        for event in self._json_objects_from_text(output):
            extracted = self._text_from_event(event)
            if extracted and (not texts or texts[-1] != extracted):
                if self._is_final_text_event(event):
                    final_texts.append(extracted)
                else:
                    texts.append(extracted)
        if final_texts:
            return final_texts[-1].strip()
        if texts:
            return "\n".join(texts).strip()
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if self._looks_like_startup_noise(stripped):
                continue
            try:
                event = json.loads(stripped)
            except Exception:
                raw_non_json.append(line)
                continue
            extracted = self._text_from_event(event)
            if extracted and (not texts or texts[-1] != extracted):
                if self._is_final_text_event(event):
                    final_texts.append(extracted)
                else:
                    texts.append(extracted)
        if final_texts:
            return final_texts[-1].strip()
        if texts:
            return "\n".join(texts).strip()
        return "\n".join(raw_non_json).strip() or output.strip()

    @staticmethod
    def _is_final_text_event(event):
        if not isinstance(event, dict):
            return False
        etype = str(event.get("type") or event.get("event") or "").lower()
        if etype in {"final", "result", "done", "response.completed", "turn.completed"}:
            return True
        if etype.endswith(".completed") or etype.endswith("_completed"):
            return True
        return bool(event.get("final") or event.get("is_final"))

    @staticmethod
    def _looks_like_startup_noise(text):
        markers = (
            "hookSpecificOutput",
            "hookEventName",
            "SessionStart",
            "<context_window_protection>",
            "<EXTREMELY_IMPORTANT>",
            "context-mode",
            "superpowers",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _json_objects_from_text(text):
        decoder = json.JSONDecoder()
        index = 0
        while index < len(text or ""):
            start = text.find("{", index)
            if start < 0:
                break
            try:
                obj, end = decoder.raw_decode(text[start:])
            except Exception:
                index = start + 1
                continue
            if isinstance(obj, dict):
                yield obj
            index = start + max(end, 1)

    def _text_from_event(self, event):
        if not isinstance(event, dict):
            return ""
        if "hookSpecificOutput" in event:
            return ""
        if event.get("type") == "assistant":
            parts = []
            for block in event.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "".join(parts).strip()
        if event.get("type") in ("system", "step-start", "step-finish", "tool"):
            return ""
        for key in ("text", "message", "content", "output", "result", "response"):
            value = event.get(key)
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, dict):
                nested = self._text_from_event(value)
                if nested:
                    return nested
        data = event.get("data") or event.get("event") or event.get("msg")
        if isinstance(data, dict):
            return self._text_from_event(data)
        return ""

    @staticmethod
    def _first_json_object(text):
        decoder = json.JSONDecoder()
        for index, ch in enumerate(text or ""):
            if ch != "{":
                continue
            try:
                obj, _end = decoder.raw_decode(text[index:])
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj
        return None

    def _parse_proxy_response(self, text):
        payload = self._first_json_object(text)
        if not payload:
            return {"type": "final", "text": text.strip()}
        rtype = payload.get("type")
        if rtype == "final":
            return {"type": "final", "text": str(payload.get("text", "")).strip()}
        if rtype == "tool_call":
            return {"type": "tool_calls", "calls": [payload]}
        if rtype == "tool_calls":
            calls = payload.get("calls") or payload.get("tool_calls") or []
            return {"type": "tool_calls", "calls": calls if isinstance(calls, list) else []}
        if "name" in payload and ("arguments" in payload or "input" in payload):
            return {"type": "tool_calls", "calls": [payload]}
        return {"type": "final", "text": text.strip()}

    def send(self, message, history, emit, should_stop):
        err = self.validate()
        if err:
            emit(AgentEvent(EventType.ERROR, {"error": err}))
            return history

        messages = list(history or [])
        messages.append({"role": "user", "content": message})
        max_iters = self.config.get("max_iterations")

        for _ in agent_iteration_steps(max_iters):
            if should_stop():
                emit(AgentEvent(EventType.THINKING, {"text": "Stopped."}))
                return messages

            response_text, error = self._run_model_proxy(messages, should_stop)
            if error:
                emit(AgentEvent(EventType.ERROR, {"error": error[:2000]}))
                return messages
            parsed = self._parse_proxy_response(response_text)

            if parsed["type"] == "final":
                text = parsed.get("text", "")
                if text:
                    emit(AgentEvent(EventType.TEXT, {"text": text}))
                messages.append({"role": "assistant", "content": text})
                emit(AgentEvent(EventType.DONE))
                return messages

            calls = parsed.get("calls") or []
            messages.append({"role": "assistant", "content": json.dumps(parsed, default=str)})
            if not calls:
                emit(AgentEvent(EventType.ERROR, {"error": "CLI returned an empty tool call."}))
                return messages

            for call in calls:
                if should_stop():
                    return messages
                name = call.get("name")
                args = call.get("arguments", call.get("input", {}))
                if not isinstance(args, dict):
                    args = {}
                payload, is_error, is_cancelled, _result = _dispatch_one_tool(
                    self.toolkit, self.executor, name, args, emit, should_stop
                )
                if should_stop() or is_cancelled:
                    return messages
                messages.append({
                    "role": "tool",
                    "name": name,
                    "content": payload,
                })

        emit(AgentEvent(EventType.ERROR, {"error": "CLI proxy reached the maximum tool iterations."}))
        return messages

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
            self._terminate_process_group(proc, kill=True)

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

    def _finalize_process(self, proc, stopped=False):
        """Reap the child, escalating to SIGKILL if it refuses to die."""
        if stopped:
            self._terminate_process_group(proc, kill=True)
            try:
                proc.wait(timeout=0.05)
            except Exception:
                pass
            self._close_process_pipes(proc)
            with self._lock:
                if self._proc is proc:
                    self._proc = None
            return
        # Bounded shutdown: ask the process group to exit, then kill quickly if needed.
        self._terminate_process_group(proc, kill=False)
        try:
            proc.wait(timeout=0.8)
        except subprocess.TimeoutExpired:
            self._terminate_process_group(proc, kill=True)
        try:
            proc.wait(timeout=1.5)
        except Exception:
            pass
        self._close_process_pipes(proc)
        with self._lock:
            if self._proc is proc:
                self._proc = None

    @staticmethod
    def _terminate_process_group(proc, kill=False):
        # SIGKILL / process groups are POSIX-only; on Windows fall straight
        # through to proc.kill() / proc.terminate().
        if platform.system() != "Windows":
            sig = signal.SIGKILL if kill else signal.SIGTERM
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, sig)
                return
            except Exception:
                pass
        try:
            if kill:
                proc.kill()
            else:
                proc.terminate()
        except Exception:
            pass

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
    # Claude Code uses snake_case session_id; opencode uses camelCase sessionID.
    sid = event.get("session_id") or event.get("sessionID")
    if sid and session_id_holder is not None:
        session_id_holder[0] = sid

    # ── Claude Code stream-json format ──────────────────────────────────────
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
        return

    # ── opencode stream format ───────────────────────────────────────────────
    # Events carry a "part" object that holds the actual payload.
    elif etype == "text":
        part = event.get("part") or {}
        if part.get("type") == "text":
            text = part.get("text", "")
            if text:
                emit(AgentEvent(EventType.TEXT, {"text": text}))
    elif etype == "tool_use":
        part = event.get("part") or {}
        tool_name = part.get("tool", "")
        state = part.get("state") or {}
        # Skip internal "invalid" sentinel tool (model called a missing tool).
        if tool_name and tool_name != "invalid":
            emit(AgentEvent(EventType.TOOL_USE, {
                "name": tool_name,
                "input": state.get("input") or {},
            }))
            if state.get("status") == "completed":
                output = state.get("output", "")
                emit(AgentEvent(EventType.TOOL_RESULT, {
                    "name": tool_name,
                    "result": str(output)[:4000],
                    "is_error": bool(state.get("error")),
                }))
    elif etype in ("step_start", "step_finish"):
        return  # opencode step markers — no user-visible content

    # ── Codex exec --json format ─────────────────────────────────────────────
    # Events: thread.started / turn.started / item.started / item.updated /
    #         item.completed / turn.completed / turn.failed / error
    elif etype == "item.completed":
        item = event.get("item") or {}
        itype = item.get("type", "")
        if itype == "agent_message":
            text = item.get("text", "")
            if text:
                emit(AgentEvent(EventType.TEXT, {"text": text}))
        elif itype in ("command_execution", "mcp_tool_call"):
            name = item.get("cmd") or item.get("tool") or itype
            output = item.get("output") or item.get("stdout") or item.get("result") or ""
            emit(AgentEvent(EventType.TOOL_USE, {
                "name": str(name),
                "input": item.get("arguments") or {"cmd": item.get("cmd", "")},
            }))
            if output:
                emit(AgentEvent(EventType.TOOL_RESULT, {
                    "name": str(name),
                    "result": str(output)[:4000],
                    "is_error": bool(item.get("exit_code", 0)),
                }))
    elif etype in ("turn.failed", "error"):
        msg = event.get("message") or event.get("error") or event.get("detail") or ""
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(msg, default=str)
        emit(AgentEvent(EventType.ERROR, {"error": str(msg)[:2000]}))
    elif etype in ("thread.started", "turn.started", "item.started",
                   "item.updated", "turn.completed"):
        return  # Codex lifecycle markers — no user-visible content

    else:
        # Generic fallback: try well-known text keys (covers Gemini --output-format
        # json with its top-level "response" key, Pi --mode json, and similar
        # single-JSON-blob CLIs) before giving up.
        for key in ("text", "response", "content", "output", "result", "message"):
            val = event.get(key)
            if isinstance(val, str) and val.strip():
                emit(AgentEvent(EventType.TEXT, {"text": val.strip()}))
                return
        # Truly unrecognised — silently drop rather than dump raw JSON.
