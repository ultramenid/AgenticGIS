"""End-to-end smoke test for the stdlib MCP bridge (zero-dependency build).

Boots a headless QGIS, starts ``McpBridgeServer`` on a background QThread, and
drives it over real HTTP/JSON-RPC from a *worker* thread — exactly the path a
CLI agent (Claude Code / OpenCode) takes. The worker exercises
``initialize`` → ``tools/list`` → ``tools/call run_pyqgis`` and a convenience
tool, proving the main-thread marshaling (``MainThreadExecutor``) works while
the Qt event loop runs on the main thread.

Run with QGIS's Python:

    /Applications/QGIS-LTR.app/Contents/MacOS/bin/python3 dev/smoke_mcp.py

Exit code 0 = all checks passed.
"""

import json
import os
import sys
import threading
import urllib.request

# Make the plugin importable as a package (parent dir on sys.path).
_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PKG_DIR)
_PKG = os.path.basename(_PKG_DIR)
sys.path.insert(0, _PARENT)

from qgis.core import QgsApplication  # noqa: E402
from qgis.PyQt.QtCore import QMetaObject, Qt, QCoreApplication  # noqa: E402

mod = __import__(_PKG, fromlist=["core", "server"])
from importlib import import_module  # noqa: E402

executor_mod = import_module(f"{_PKG}.core.executor")
toolkit_mod = import_module(f"{_PKG}.core.toolkit")
server_mod = import_module(f"{_PKG}.server.mcp_server")

PASS, FAIL = "✓", "✗"
_results = []


def check(cond, label, detail=""):
    _results.append(bool(cond))
    mark = PASS if cond else FAIL
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))


def rpc(base_url, method, params=None, mid=1, notify=False):
    payload = {"jsonrpc": "2.0", "method": method}
    if not notify:
        payload["id"] = mid
    if params is not None:
        payload["params"] = params
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        status = resp.status
    return status, (json.loads(body) if body.strip() else None)


def worker(base_url, app):
    """Runs on a background thread — the agent's-eye view of the bridge."""
    try:
        # 1. initialize
        st, r = rpc(base_url, "initialize",
                    {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "smoke", "version": "0"}}, mid=1)
        check(st == 200 and r and r.get("result", {}).get("serverInfo", {}).get("name") == "AgenticGIS",
              "initialize returns serverInfo", f"status={st}")

        # 2. notifications/initialized (no id → 202, no body)
        st, r = rpc(base_url, "notifications/initialized", notify=True)
        check(st == 202 and r is None, "notifications/initialized → 202 empty", f"status={st}")

        # 3. tools/list
        st, r = rpc(base_url, "tools/list", {}, mid=2)
        tools = r.get("result", {}).get("tools", []) if r else []
        names = {t["name"] for t in tools}
        check(st == 200 and "run_pyqgis" in names and len(tools) >= 10,
              "tools/list returns all tools", f"{len(tools)} tools")

        # 4. tools/call run_pyqgis — arbitrary PyQGIS, marshaled to main thread
        code = "result = QgsApplication.instance().platform()\nprint('hello from pyqgis')"
        st, r = rpc(base_url, "tools/call",
                    {"name": "run_pyqgis", "arguments": {"code": code}}, mid=3)
        content = r.get("result", {}).get("content", []) if r else []
        payload = json.loads(content[0]["text"]) if content else {}
        check(st == 200 and payload.get("ok") and "hello from pyqgis" in (payload.get("stdout") or ""),
              "tools/call run_pyqgis executes on main thread",
              f"ok={payload.get('ok')} result={payload.get('result')!r}")

        # 5. tools/call list_layers — convenience tool through executor.
        # (get_project_state needs a live iface/canvas, absent in headless;
        # list_layers exercises the same dispatch path with only QgsProject.)
        st, r = rpc(base_url, "tools/call",
                    {"name": "list_layers", "arguments": {}}, mid=4)
        content = r.get("result", {}).get("content", []) if r else []
        layers = json.loads(content[0]["text"]) if content else None
        check(st == 200 and isinstance(layers, list),
              "tools/call list_layers returns layer list",
              f"layers={layers!r}")

        # 6. error surfacing — bad PyQGIS → ok False, isError True
        st, r = rpc(base_url, "tools/call",
                    {"name": "run_pyqgis", "arguments": {"code": "raise ValueError('boom')"}}, mid=5)
        is_error = r.get("result", {}).get("isError") if r else None
        payload = json.loads(r["result"]["content"][0]["text"]) if r else {}
        check(st == 200 and is_error and payload.get("ok") is False,
              "errors surface as isError", f"isError={is_error}")

        # 7. unknown method → JSON-RPC error -32601
        st, r = rpc(base_url, "does/not/exist", {}, mid=6)
        check(st == 200 and r and r.get("error", {}).get("code") == -32601,
              "unknown method → -32601", f"code={r.get('error', {}).get('code') if r else None}")
    except Exception as exc:  # noqa: BLE001
        check(False, "worker raised", f"{type(exc).__name__}: {exc}")
    finally:
        # Return control to the main thread so app.exec_() unblocks.
        QMetaObject.invokeMethod(app, "quit", Qt.QueuedConnection)


def main():
    print("AgenticGIS MCP bridge smoke test (stdlib, zero-dep)")
    print("-" * 52)

    QgsApplication.setPrefixPath("/Applications/QGIS-LTR.app/Contents/MacOS", True)
    app = QgsApplication([], False)
    app.initQgis()

    # MainThreadExecutor MUST be created on the main thread.
    executor = executor_mod.MainThreadExecutor()
    toolkit = toolkit_mod.QgisToolkit(iface=None)

    server = server_mod.McpBridgeServer(toolkit, executor, host="127.0.0.1", port=0)
    server.start()
    # Wait until the background HTTP server is actually serving.
    for _ in range(100):
        if server._server is not None:
            break
        QThread_msleep(50)
    base_url = server.base_url
    print(f"  bridge: {base_url}")

    t = threading.Thread(target=worker, args=(base_url, app), daemon=True)
    t.start()

    app.exec_()          # main thread services executor jobs until worker quits
    t.join(timeout=5)

    server.stop()
    app.exitQgis()

    print("-" * 52)
    passed, total = sum(_results), len(_results)
    print(f"{passed}/{total} checks passed")
    sys.exit(0 if passed == total and total > 0 else 1)


def QThread_msleep(ms):
    from qgis.PyQt.QtCore import QThread
    QThread.msleep(ms)


if __name__ == "__main__":
    main()
