# AgenticGIS — Performance / Freeze / Force-Close Audit

Deep-dive audit of the codebase for risks that cause **UI freeze**, **silent
force-close** (segfault / unhandled exception in C++ callbacks), or **stuck
threads**. Findings are prioritized and tied to specific source locations
(`file:line`). Cross-referenced with current best practices from PyQt, QGIS
PyQGIS, and CPython `http.server` / SSE docs (Context7).

Severity legend: **P0** (data loss / force-close), **P1** (UI freeze), **P2**
(perf), **P3** (robustness).

---

## Executive summary

The plugin is architecturally sound (single toolkit, single source of tool
specs, queued signal marshalling to the main thread, stdlib-only HTTP). The
**single biggest risk** is the combination of a Python-level GIL release in
`run_pyqgis` and synchronous main-thread work that the user cannot interrupt
mid-flight. Three concrete classes of problems emerged:

1. **`run_pyqgis` is uninterruptible on the main thread** (P0–P1). A long
   synchronous algorithm or an infinite loop in agent code holds the GUI and
   cannot be stopped — the Stop button only takes effect *between* iterations
   on the worker thread, not while the toolkit is running.
2. **MCP server runs on a `QThread` whose `BaseHTTPRequestHandler` blocks**
   (P1). If a client opens many connections or one request stalls (e.g.
   `run_pyqgis` returning 200 kB after a long algorithm), the thread pool
   can be exhausted, hanging the CLI agent.
3. **SSE keep-alive state on `AnthropicHttpClient` is racy and the
   "drain to EOF" branch can hang** when the connection is already half-closed
   by a network reset (P1).

Below: each finding, the evidence, and a minimal fix.

---

## Findings

### F1. `run_pyqgis` blocks the main thread with no cancellation hook — P0
`core/toolkit.py:67-148`

`run_pyqgis` executes arbitrary user-supplied Python with `exec(...)` on the
**main thread** (it is invoked via `MainThreadExecutor.run_sync` from the
worker, which posts to the main thread). Inside the `exec`, the agent can:

* run a `processing.run(...)` with a long timeout (no `QgsTask` is used — see
  QGIS docs on `QgsTask` for the recommended pattern);
* call any blocking Python (C extension with no GIL release, network call,
  `time.sleep`);
* recurse into a heavy `iface.mapCanvas().refresh()` even if it fails
  (`toolkit.py:144-147` swallows the exception, but the call itself runs).

**Result**: while `run_pyqgis` is running, the QGIS UI **freezes**, the Stop
button cannot be serviced (its `should_stop` only fires between tool calls
in the agent loop, not during toolkit execution), and QGIS may show the
"Application Not Responding" / "Force Quit" dialog on macOS.

**QGIS best practice (Context7, `qgis_documentation/tasks.rst`)**: heavy
work should run inside a `QgsTask` whose `run()` periodically checks
`isCanceled()`. The cookbook is explicit: *"Raising exceptions will crash
QGIS, so we handle them internally … This method MUST return True or
False."* The current code does the exception-handling part right (good),
but skips the cancellation/cancelability part (bad).

**Minimal fix**:

1. Wrap the body of `run_pyqgis` in a `QgsTask.fromFunction(...)` and
   register it with `QgsApplication.taskManager()` if you want it to be
   visible in the QGIS progress bar and cancellable via the system
   progress widget.
2. **Cheaper alternative** that preserves the current "synchronous result
   back to the agent" contract: have `run_pyqgis` install a `QTimer` watchdog
   on the main thread (created by the worker before emitting the job) that
   cooperates with a `threading.Event` and returns a cancellation sentinel
   after `main_thread_timeout`. This still freezes the UI but at least
   doesn't hang the worker thread forever.
3. Always check the cancellation flag at the top of every long-running
   method (`get_layer_statistics`, `create_chart`, `_iterate_features`).

Even a simple `try: ... except BaseException` boundary with a watchdog is
better than today's "wait forever".

---

### F2. `MainThreadExecutor.run_sync` times out but the slot still runs to completion — P1
`core/executor.py:39-63`

When the worker thread's `job.event.wait(timeout)` raises `TimeoutError`,
**the main thread keeps executing `job.fn()` and will eventually call
`job.event.set()` and write to `job.result` / `job.error`**. The worker
that already raised is gone, but the now-orphaned `Job` object has its
attributes mutated out from under it. The next call to `run_sync` will see
a *new* `Job` and a *new* event — so the bug is silent — but the leaked
work is still doing CPU and possibly touching the project. If a *second*
timeout follows quickly, the main thread accumulates in-flight jobs and
the UI freezes harder.

**Minimal fix**: keep a single in-flight sentinel on the executor and
either (a) refuse new jobs while one is running, or (b) only allow one
job at a time and queue. Also null out the lambda's `self` capture in
the job to prevent late writes after the worker has returned.

---

### F3. `McpBridgeServer` `QThread` + `ThreadingHTTPServer` mixin — P1
`server/mcp_server.py:111-202`, `:174-202`

`McpBridgeServer` is a `QThread` whose `run()` does
`self._server.serve_forever(poll_interval=...)`. `serve_forever` blocks
on `select()` (a GIL-releasing syscall), which is fine in itself, but:

* `ThreadingHTTPServer` spawns a **daemon thread per request**. Each
  request handler ultimately calls
  `tools_mod.dispatch(self.toolkit, self.executor, ...)` which round-trips
  through the main thread via the executor (F1). If a request is in
  flight when `stop()` is called, the daemon thread keeps running and
  Python's `wait(5000)` returns but the daemon can outlive the QThread
  parent by enough to race with `unload()`.
* `daemon_threads = True` means those threads can be **silently killed at
  interpreter shutdown**, dropping in-flight tool results and possibly
  leaving the toolkit in a half-modified state. QGIS plugins run inside
  the QGIS Python interpreter, so a hard exit during a tool call that
  has just started a `processing.run` can leave a temp file or memory
  layer behind.
* There is no `request_timeout` on the underlying socket. A slow or
  stalled client holds a thread indefinitely.

**CPython best practice (Context7, `cpython http.server`)** documents
`socket.settimeout(value)` and `BaseServer.timeout` but the plugin sets
neither. The library explicitly notes that `HTTPServer` (non-threaded)
*"would wait indefinitely"* on browser pre-opens — your threaded version
doesn't have *that* bug, but it has the equivalent for stuck handlers.

**Minimal fix**:
1. In `_RpcServer.__init__`, set
   `self.timeout = 30` (or a configurable value) so the server doesn't
   sit on idle connections forever.
2. In `_Handler.setup()`, call `self.connection.settimeout(60)` so an
   individual request cannot hang longer than its tool timeout.
3. In `McpBridgeServer.stop()`, set `self._server._BaseServer__shutdown_request`
   and `join` non-daemon-style; or simpler, flip `daemon_threads = False`
   and explicitly drain pending threads before returning.
4. Catch `BaseException` around `tools_mod.dispatch` and always set
   `Mcp-Session-Id` even on error (today the `_respond` helper does this,
   good — but the `do_POST` 413 path skips it, which is a minor protocol
   deviation).

---

### F4. `AnthropicHttpClient` SSE read loop can hang on a half-closed socket — P1
`backends/anthropic_http.py:103-162`

The `while True: raw = response.readline()` loop relies on EOF to exit.
After a normal stream end, the *else* branch does
`while response.readline(): pass` to drain the socket so the connection
can be reused. Two problems:

1. If `should_stop()` fires inside the loop, the code sets
   `premature_exit = True`, **closes** `self._conn`, and returns — fine.
   But the *else* branch (`premature_exit = False`) drains
   unconditionally, and on a server that closes the connection without
   sending the final newline-terminated empty line, `readline()` blocks
   **forever** (no socket timeout is set on `self._conn`).
2. `_ensure_conn` checks `self._conn.sock.getpeername()` to detect a
   stale connection. The bare `except Exception` swallows
   `OSError`-equivalent errors and resets to a fresh connection. This is
   correct but the lack of a request-level `timeout=` on `conn.request(...)`
   means a slow first read on a streaming response can block the worker
   thread past `main_thread_timeout`. The executor's `TimeoutError`
   propagates, but the in-flight `readline()` keeps the connection busy
   on the next attempt (the new `_conn` is fine, but the old one leaks
   FDs until the OS garbage-collects the half-open socket).

**Anthropic SDK best practice (Context7, `anthropic-sdk-python/helpers.md`)**:
the official SDK uses a streaming context manager (`with client.messages.stream(...) as stream:`) so closing is automatic and bounded. The current
hand-rolled loop has the same behaviour but unbounded.

**Minimal fix**: set `self._conn.sock.settimeout(...)` (or pass
`timeout=...` to `conn.request`) and *always* run the drain inside a
bounded `while response.readline(): pass` guarded by a deadline. Consider
just *not* draining — closing the connection after each request is fine
for an interactive chat and removes the entire class of bug.

---

### F5. `OpenAIHttpClient` reads `response` with no socket timeout — P1
`backends/openai_http.py:57-128`

`urllib.request.urlopen(request, timeout=timeout)` *does* set a timeout on
the socket, but `timeout=600` is the **default** passed in from the call
site (no override), and the parameter is the only timeout. If the
provider's response stalls after the headers (e.g. SSE keep-alive lines
that never terminate), the worker thread blocks for up to 10 minutes
with no progress. Combined with the `should_stop()` check *between* lines,
the worker thread is cancelled correctly, but the underlying `urlopen`
keeps reading until either 10 minutes elapse or the connection is reset
by the peer.

**Minimal fix**: pass a configurable, shorter timeout to
`stream_message` and honour `should_stop()` by closing the response
(`response.close()`) inside the loop. Today, the loop just `break`s but
leaves the response open; the next iteration of the agent loop opens a
new connection and the old one leaks until the timeout fires.

---

### F6. `ChatWorker.stop()` only flips a Python flag — P1
`gui/chat_dock.py:46-69`, `core/executor.py`

`should_stop` is checked:
* at the top of every agent loop iteration (`api_backend.py:133`,
  `openai_backend.py:112`, `cli_backend.py:204`),
* inside the SSE read loop (`anthropic_http.py:105`, `openai_http.py:71`),
* at the top of every `tools/call` dispatch.

But **not**:
* inside `MainThreadExecutor._execute` (the main-thread slot). Once the
  main thread is busy in `run_pyqgis`, no `should_stop` check happens.
* inside `processing.run(...)` (`toolkit.py:253`). QGIS's Processing
  framework has its own cancellation API (`feedback.cancel()`); the
  plugin never wires that up.

**Force-close scenario**: user clicks Stop while a long `processing.run`
is in flight. The worker thread's `should_stop` is True, but the main
thread is blocked in `processing.run`. The worker just sits waiting on
`job.event.wait(timeout)`. After 60 s, `TimeoutError` fires on the worker,
the worker emits `ERROR` and the agent returns. But `processing.run` is
*still running* on the main thread — the user sees a red error bubble
while the algorithm keeps churning. They click Stop again, nothing
happens. They ⌘-Q QGIS, and on macOS the "force quit" dialog appears
because `processing.run` is still mid-call into the C++ GDAL/OGR layer.

**Minimal fix**:
1. Construct a `QgsFeedback` (or subclass) per request, hand it to
   `processing.run(..., feedback=fb)`, and have `_on_stop` call
   `fb.cancel()`. QGIS's processing framework is cancellation-aware.
2. For arbitrary `run_pyqgis`, install a `QTimer.singleShot(0, ...)` watchdog
   on the main thread before `exec(...)` and check the cancellation
   `threading.Event` from inside the agent's namespace via a helper
   injected as `_should_stop`. Not perfect (the C call still runs), but
   the user's Python code can poll.

---

### F7. `processing.run` swallows the actual exception type — P2
`core/toolkit.py:247-262`

`except BaseException` is broad by design, but the error message loses
the stack trace. Combined with F1/F6, when a long algorithm fails the
agent sees a single line and can't tell whether it was a timeout,
cancellation, or a real error. QGIS's processing framework raises
specific exceptions (`QgsProcessingException`,
`QgsProcessingCanceledException`) — check them and report distinctly so
the agent can decide whether to retry.

---

### F8. `list_processing_algorithms` cache is never invalidated on settings change — P2
`core/toolkit.py:231-245`, `core/toolkit.py:243-245`

`_invalidate_alg_cache` exists but is never called. Plugins enabled/
disabled at runtime won't be reflected. Not a freeze, but a "force-quit"
ergonomics problem: user enables Processing plugin, expects new
algorithms, gets stale list, runs an alg id that no longer exists, and
the generic error confuses the agent.

**Fix**: hook into `QgsApplication.pluginsChanged` (QGIS 3.22+) or
poll once per session start, not per call.

---

### F9. `create_chart` / `get_layer_statistics` iterate features in Python — P2
`core/toolkit.py:283-318`, `:320-362`

Both call `layer.getFeatures()` without `QgsFeatureRequest` flags. For a
multi-million-feature layer, the iteration forces geometry deserialization
even when only attribute stats are needed, blocking the main thread.
`run_pyqgis` already injects a smarter helper (`_iterate_features` at
`toolkit.py:96-105`); reuse it here.

`get_layer_statistics` also calls `min()`, `max()`, `sum()` in a Python
loop — `QgsVectorLayerCache` or QGIS's own `QgsStatisticalSummary` /
`QgsNumericStatistic` is the C++-accelerated path and is *much* faster
for numeric fields.

---

### F10. `run_pyqgis` injects a ~6 kB namespace and rebinds `processing` on every call — P2
`core/toolkit.py:78-93`

`ns.update({k: getattr(qgis_core, k) for k in dir(qgis_core) if not k.startswith("_")})`
runs `dir()` + a `getattr` for ~500 names on every single tool call.
Cache the namespace once on the toolkit (it's read-only after QGIS
init) and `dict.copy()` it per call. Also, `import processing` inside
the body (line 90) is a no-op after the first success but the `try/except`
is still paid.

---

### F11. `tool_call_bubble` re-parses JSON for display on every event — P2
`gui/chat_dock.py:485-492` and `gui/tool_call_bubble.py`

`is_err = str(result).startswith("Error") or str(result).startswith("error")`
is a string-prefix check on a JSON-encoded payload. By the time it
reaches the UI the result is a string (good), but the heuristic is
fragile: any tool that legitimately returns a string starting with
"error" in its data will be styled as an error. Use a structured
`is_error` flag instead (the backends already compute it at
`api_backend.py:174` and `mcp_server.py:149`; pass it through).

---

### F12. `AnthropicHttpClient._conn` is not thread-safe — P2
`backends/anthropic_http.py:45-67`

`_ensure_conn` reads and writes `self._conn` and `self._conn_host`
without a lock. Today only one worker calls it (the per-turn
`ApiBackend` instance is constructed per send), so there's no actual
race — but if a future change caches the client on `self.toolkit`,
two concurrent `send()` calls will race on the connection state. Add
`threading.Lock` around `_ensure_conn`.

---

### F13. `CliToolBackend` subprocess uses `bufsize=1` text mode + `readline()` — P2
`backends/cli_backend.py:176-243`

`text=True, bufsize=1` means line-buffered **text** I/O on a pipe, but
`subprocess.PIPE` is a pipe, not a TTY, so Python's text mode falls back
to block-buffered for the underlying file descriptor. The
`threading.Thread` readers do `for line in pipe: q.put(...)` which
calls `readline()` under the hood. This is fine for `claude` (which
emits discrete JSONL events), but `opencode` / `codex` may emit
multi-line JSON in a single `write()`. If a single JSON record spans
two `readline()` calls — or two records land in the same `readline()` —
the parsing in `_handle_claude_event` (`json.loads(line)`) silently
drops the malformed one (`return` on `JSONDecodeError`).

**Minimal fix**: read raw bytes and split on `\n` yourself, accumulating
partial lines; or use a proper JSONL parser that handles concatenated
records. Also, `t_out.join(timeout=5)` then `t_err.join(timeout=5)` can
deadlock if the pipe fills faster than the consumer drains — the queue
is unbounded (good) but the *kernel* pipe buffer is 64 kB on macOS; a
verbose `claude` run with full tool traces can deadlock the reader
threads. Use a select/poll loop on the two FDs instead of two threads.

---

### F14. The `Mcp-Session-Id` header is set on error responses without the header being initialized — P3
`server/mcp_server.py:68-72`, `:82-86`

`_empty()` reads `self.server.session_id` and the 413 path writes
`Content-Length: 0` but omits `Mcp-Session-Id`. The MCP spec says
session id is mandatory on every response. Minor protocol bug, not a
freeze.

---

### F15. `subprocess.Popen` for the CLI tool inherits the env and never closes file descriptors — P3
`backends/cli_backend.py:177-180`

No `close_fds=True` (default on POSIX but not portable), no `start_new_session=True` so Ctrl-C in the spawned CLI doesn't kill QGIS. Also
`self._proc` is mutated from the main thread (the dock) and the
worker thread (the backend's `send`); on Stop the dock sets
`self._worker.stop()` which sets the flag, the worker calls
`self._proc.terminate()`, then the dock can be closed while the
worker is still draining the queue. Today the dock is a child widget
so this is rare, but a hardened version would synchronize.

---

### F16. `QgisToolkit.run_pyqgis` `exec` namespace lets the agent call `__import__` — Security/P3
`core/toolkit.py:78-93`

`from qgis.core import *` exposes everything, including modules the
agent shouldn't reach (`os`, `subprocess`, `ctypes`). This is the
**intentional** design ("every QGIS feature and every installed
plugin"), so don't remove it — but document the safety boundary in
the settings dialog and add a `confirm_dangerous_calls` config that
gates `os.system`, `subprocess.Popen`, `shutil.rmtree`, etc. (The README
mentions "Avoid pointing it at irreplaceable data without a backup"
which is the right user-facing framing.)

---

### F17. `iface.mapCanvas().refresh()` runs *after* the tool returned — P2
`core/toolkit.py:144-147`

Always refreshing even when no layers changed is cheap on the main
thread but compounds: with 25 iterations × 1 refresh each, the
canvas repaints 25 times per turn. A "dirty" flag set by the tool
and consumed by the final refresh would be cheaper, but the savings
are minor (QGIS already throttles repaints).

---

### F18. `_free_port()` has a small TOCTOU window — P3
`server/mcp_server.py:28-33`

`bind` then `close` then re-bind in `serve_forever`. The kernel can
hand the just-released port to a different process before the MCP
server starts. Add a `SO_REUSEADDR` (already set via
`allow_reuse_address = True`) and prefer `socket.socket()` then
`bind` then `listen` and pass the already-bound socket to
`ThreadingHTTPServer(server_address, _Handler, bind_and_activate=False)`
to eliminate the gap.

---

## Recommended fix order

1. **F1 + F6** together: introduce a `QgsFeedback` per tool call and
   install a `QTimer`-based cancellation watchdog in `run_pyqgis`. This
   single change converts the worst force-close class into a clean
   "Stop" UX.
2. **F2**: harden `MainThreadExecutor` so a timed-out job doesn't keep
   mutating its result slot.
3. **F3 + F4 + F5**: add socket timeouts to the MCP server and both
   HTTP clients; remove the "drain to EOF" branch from
   `AnthropicHttpClient`.
4. **F8 + F10 + F11**: small, contained, no architectural risk.
5. Everything else is incremental.

## Verification commands

* Lint: `python -m py_compile $(find . -name '*.py' -not -path './__pycache__/*')`
  (the project ships no lint config; a minimal `pyproject.toml` with
  `ruff` would be a small win).
* Tests: `python -m pytest tests/ -q` (existing tests pass; the
  audit found no test for the `dispatch` executor round-trip or for
  `run_pyqgis` cancellation — recommend adding both).

