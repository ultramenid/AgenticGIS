"""A dependency-free MCP server exposing the QGIS toolkit to CLI agents.

MCP is JSON-RPC 2.0 over HTTP, so we implement it with the Python standard
library (``http.server``) — no ``mcp``/``uvicorn`` packages, nothing to pip
install, runs on a stock QGIS Python (incl. 3.9). External CLI agents (Claude
Code, OpenCode) connect to this server over the "streamable HTTP" transport.

The server runs on a background ``QThread`` so the QGIS UI stays responsive;
each ``tools/call`` hops onto the main thread via the ``MainThreadExecutor``.
Tools are generated from the shared ``TOOL_SPECS`` so they stay identical to
the in-process API backend. Responses are returned as JSON, or as a single SSE
event when the client's ``Accept`` header asks for ``text/event-stream``.
"""

import json
import socket
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qgis.PyQt.QtCore import QThread

from ..core import tools as tools_mod

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "AgenticGIS", "version": "0.1.0"}


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    MAX_BODY_BYTES = 10 * 1024 * 1024   # 10 MiB

    def log_message(self, *args):  # silence default stderr logging
        pass

    # -- response helpers ------------------------------------------------ #
    def _respond(self, rpc_response):
        accept = self.headers.get("Accept", "")
        if "text/event-stream" in accept:
            body = json.dumps(rpc_response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Mcp-Session-Id", self.server.session_id)
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(b"event: message\r\ndata: " + body + b"\r\n\r\n")
            return

        # Stream plain JSON without building full string in memory
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", self.server.session_id)
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        for chunk in json.JSONEncoder(default=str).iterencode(rpc_response):
            self.wfile.write(chunk.encode("utf-8"))

    def _empty(self, status=202):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Mcp-Session-Id", self.server.session_id)
        self.end_headers()

    # -- HTTP verbs ------------------------------------------------------ #
    def do_POST(self):
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError:
            length = 0

        if length > self.MAX_BODY_BYTES:
            self.send_response(413, "Payload Too Large")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        raw = self.rfile.read(length) if length else b""
        try:
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            self._respond({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "Parse error"}})
            return
        rpc_response = self.server.handle_rpc(message)
        if rpc_response is None:        # notification — no body
            self._empty(202)
        else:
            self._respond(rpc_response)

    def do_GET(self):
        # We don't push server-initiated messages; no SSE stream to open.
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        self._empty(200)


class _RpcServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, toolkit, executor):
        super().__init__(addr, _Handler)
        self.toolkit = toolkit
        self.executor = executor
        self.session_id = uuid.uuid4().hex

    def handle_rpc(self, message):
        method = message.get("method")
        mid = message.get("id")
        is_notification = "id" not in message
        try:
            if method == "initialize":
                params = message.get("params", {})
                result = {
                    "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                }
            elif method in ("notifications/initialized", "notifications/cancelled"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [
                    {"name": s["name"], "description": s["description"],
                     "inputSchema": s["input_schema"]}
                    for s in tools_mod.TOOL_SPECS
                ]}
            elif method == "tools/call":
                params = message.get("params", {})
                out = tools_mod.dispatch(
                    self.toolkit, self.executor,
                    params.get("name"), params.get("arguments", {}),
                )
                is_error = isinstance(out, dict) and out.get("ok") is False
                # Cap oversized tool results to prevent memory spikes
                out_text = json.dumps(out, default=str)
                if len(out_text) > 200_000:
                    out_text = out_text[:200_000] + "\n... [output truncated by server]"
                result = {
                    "content": [{"type": "text", "text": out_text}],
                    "isError": bool(is_error),
                }
            else:
                if is_notification:
                    return None
                return {"jsonrpc": "2.0", "id": mid,
                        "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except Exception as exc:  # noqa: BLE001
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": result}


class McpBridgeServer(QThread):
    def __init__(self, toolkit, executor, host="127.0.0.1", port=0,
                 poll_interval=0.5, parent=None):
        super().__init__(parent)
        self.toolkit = toolkit
        self.executor = executor
        self.host = host
        self.port = port or _free_port()
        self.poll_interval = poll_interval
        self._server = None

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}/mcp"

    @staticmethod
    def dependencies_available():
        return True  # stdlib only — always available

    def run(self):  # QThread entry point — runs on the background thread
        self._server = _RpcServer((self.host, self.port), self.toolkit, self.executor)
        self._server.serve_forever(poll_interval=self.poll_interval)

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.wait(5000)
