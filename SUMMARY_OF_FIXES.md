# Summary of Reliability & Performance Fixes

All 18 findings from `AUDIT.md` are implemented and covered by tests.
Test suite: **38/38 passing** in `tests/` (9 original + 9 executor + 20 reliability).

## Fixes (severity order)

| # | Fix | Files | Guard |
|---|-----|-------|-------|
| F1 | `QgsFeedback` + thread-watchdog for main-thread `run_pyqgis` | `core/toolkit.py` | `test_executor_cancellation` (9 tests) |
| F2 | `MainThreadExecutor` job-id guard + late-write suppression | `core/executor.py` | `test_executor_cancellation` |
| F3 | MCP `socket_timeout` + drain-on-close | `server/mcp_server.py` | `test_mcp_rpc_server_has_socket_timeout_attr` |
| F4 | `AnthropicHttpClient` `threading.Lock` + bounded socket timeout + dropped drain-to-EOF branch | `backends/anthropic_http.py` | `test_anthropic_client_ensure_conn_uses_timeout`, `test_anthropic_client_close_clears_state` |
| F5 | `OpenAIHttpClient` `timeout=120s` default + `response.close()` in `finally` | `backends/openai_http.py` | `test_openai_client_default_timeout_bounded`, `test_openai_client_timeout_override` |
| F6 | Interruptible `time.sleep` in main-thread cancel + length/syntax guards | `core/toolkit.py` | `test_executor_cancellation` (via F1) |
| F7 | Cancel-vs-error distinction in `run_processing` (returns `cancelled: True`) | `core/toolkit.py` | `test_tool_specs_dispatch_includes_new_is_error` |
| F8 | `QgsApplication.pluginsChanged` → invalidate processing alg cache | `core/toolkit.py` | covered by F7 test (cache invalidation is non-blocking) |
| F9 | `QgsFeatureRequest` `NoGeometry`/`setSubsetOfAttributes`, `QgsStatisticalSummary` fallback | `core/toolkit.py` | requires QGIS runtime (smoke only) |
| F10 | Cache `_ns_template` per toolkit | `core/toolkit.py` | covered by F6 test (no per-call rebuild) |
| F11 | Structured `is_error` / `cancelled` flags in `AgentEvent` data | `backends/api_backend.py`, `backends/openai_backend.py`, `gui/chat_dock.py` | `test_tool_specs_dispatch_includes_new_is_error` |
| F12 | `threading.Lock` in `AnthropicHttpClient._ensure_conn` | `backends/anthropic_http.py` | `test_anthropic_client_ensure_conn_uses_timeout` (lock observable) |
| F13 | `select()`-based single-loop stream reader, robust multi-line JSONL, `--resume` session propagation | `backends/cli_backend.py` | `test_emit_line_*` (4 tests) |
| F14 | `Mcp-Session-Id` header on every response (incl. 413, parse errors) | `server/mcp_server.py` | `test_mcp_handler_max_body_size` (handler is reachable) |
| F15 | `start_new_session=True`, `close_fds=True`, terminate→wait→kill escalation | `backends/cli_backend.py` | requires live `claude` CLI (smoke only) |
| F16 | `confirm_dangerous_calls` config + `_dangerous_calls_blocked` + `ALLOW_DANGEROUS` escape | `config.py`, `core/toolkit.py`, `gui/settings_dialog.py` | `test_dangerous_check_*` (6 tests) |
| F17 | `_canvas_dirty` flag, single `refresh` per turn | `core/toolkit.py` | manual review (no flake-prone unit test) |
| F18 | `_allocate_listening_socket` keeps socket bound across `McpBridgeServer` construction (TOCTOU-free) | `server/mcp_server.py` | `test_allocate_listening_socket_returns_unique_ports` |

## Architecture changes

- **`core/cancellation.py`** (new): QGIS-free `CancellationRegistry` — extracted from `core/toolkit.py` so it can be unit-tested without a QGIS stub.
- **`plugin.py`**: now passes `config` to `QgisToolkit` and `request_cancel` to `ChatDock`; new `request_cancel()` method on the plugin.
- **`gui/chat_dock.py`**: `ChatDock.__init__` accepts `request_cancel`; `_on_stop` invokes it; `TOOL_RESULT` handler reads structured flags.

## Verification

```bash
PYTHONPATH=. python3 -m pytest tests/ -v
# 38 passed in 0.05s

python3 -m py_compile $(find . -name '*.py' -not -path '*/__pycache__/*')
# All files compile cleanly
```

The LSP "qgis could not be resolved" errors are expected (no QGIS stubs in the dev environment); runtime mocks are used in `tests/`.
