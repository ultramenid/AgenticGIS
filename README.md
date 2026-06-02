# AgenticGIS

An in-QGIS agentic chat assistant. Type a request in plain language and an LLM
agent carries it out by generating and running **PyQGIS** code inside the
running QGIS session — giving it access to **every QGIS feature and every
installed plugin**.

**Zero dependencies.** The plugin runs entirely on QGIS's bundled Python
standard library — no `pip`, no `conda`, no Python upgrade. Drop it in, enable
it, and connect.

## How it works

```
QGIS (main thread)
 ├─ Chat dock (you type here)
 ├─ QgisToolkit ............ run_pyqgis / project state / processing / layers
 ├─ MainThreadExecutor ..... marshals every QGIS op onto the main thread
 ├─ Backend (pluggable):
 │    • CLI tool  → spawns Claude Code / OpenCode → talks to the MCP bridge
 │    • API key   → in-process Anthropic tool-use loop (stdlib urllib)
 │    • Subscription → same loop, OAuth/bearer via env
 └─ MCP bridge (background thread) ... stdlib http.server exposing the toolkit
```

The catch-all tool is `run_pyqgis`, which executes arbitrary PyQGIS — that is
what makes "all features + all plugins" possible. Convenience tools
(`get_project_state`, `run_processing`, `list_plugins`, …) keep common requests
cheap and reliable. The same tool set is exposed both in-process (API mode) and
over MCP (CLI mode) from one definition in `core/tools.py`.

Both transports are hand-rolled on the standard library:
`backends/anthropic_http.py` (Messages API over `urllib`, SSE streaming) and
`server/mcp_server.py` (MCP = JSON-RPC 2.0 over `http.server`). This is why
there is nothing to install and it works on any QGIS, including the Python 3.9
that the official macOS `.dmg` ships
([qgis/QGIS#54491](https://github.com/qgis/QGIS/issues/54491)).

## Connection modes (Settings → Connect via)

1. **CLI tool** — use an installed, already-logged-in agent (Claude Code /
   OpenCode). No API key needed; the plugin auto-starts the local MCP bridge.
2. **API key** — Anthropic key (or `ANTHROPIC_API_KEY`).
3. **Subscription / OAuth** — rides an existing login via `ANTHROPIC_AUTH_TOKEN`
   (+ optional `ANTHROPIC_BASE_URL`). For a key-less subscription, CLI-tool mode
   is usually simpler.

## Install

1. Copy/symlink this folder into the QGIS plugins directory as `AgenticGIS`:
   `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
2. Enable **AgenticGIS** in Plugins → Manage and Install Plugins.
3. Click the toolbar icon to open the chat dock, then **⚙ Settings** to pick a
   connection mode.

That's it — no dependency step.

## Safety

Generated code **auto-runs** (no confirmation), scoped to the current QGIS
project/layers. Avoid pointing it at irreplaceable data without a backup.
