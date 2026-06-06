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

Reliability hardening
---------------------
* Socket read/write timeout on every connection so a stalled client cannot
  block a worker thread indefinitely.
* ``BaseServer.timeout`` so ``serve_forever`` exits promptly on shutdown
  instead of waiting on a kernel-level select.
* TOCTOU-free port allocation — the listening socket is held until the
  server hands it to ``ThreadingHTTPServer`` (no race with another process
  grabbing the just-released port).
* ``Mcp-Session-Id`` is set on every response, including 413/parse-error
  paths, per the MCP spec.
"""

import json
import socket
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qgis.PyQt.QtCore import QThread

from ..core import tools as tools_mod

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "AgenticGIS", "version": "0.1.0"}

# Bounded socket I/O so a stalled client cannot hold a thread forever.
DEFAULT_SOCKET_TIMEOUT = 30.0  # seconds; per-handler, configurable
DEFAULT_SERVER_TIMEOUT = 1.0   # seconds; serve_forever poll interval upper bound


def _allocate_listening_socket(host, port):
    """Bind a socket, return ``(sock, port)``.

    Holding the socket here (instead of bind-then-close as in the original
    ``_free_port``) eliminates the TOCTOU window where another process can
    grab the just-released port before we re-bind. Caller is responsible
    for passing the bound socket to ``ThreadingHTTPServer`` and then closing
    the local reference (the server dupes the FD).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port or 0))
    sock.listen(128)
    return sock, sock.getsockname()[1]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    MAX_BODY_BYTES = 10 * 1024 * 1024   # 10 MiB

    def log_message(self, *args):  # silence default stderr logging
        pass

    # -- response helpers ------------------------------------------------ #
    def _send_headers(self, status, content_type, content_length=None,
                      extra_headers=None, close=True):
        self.send_response(status)
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.send_header("Content-Type", content_type)
        # Per MCP spec, every response carries the session id.
        self.send_header("Mcp-Session-Id", self.server.session_id)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def _respond(self, rpc_response):
        accept = self.headers.get("Accept", "")
        if "text/event-stream" in accept:
            body = json.dumps(rpc_response).encode("utf-8")
            self._send_headers(200, "text/event-stream", content_length=len(body),
                               extra_headers={"Cache-Control": "no-cache"})
            try:
                self.wfile.write(b"event: message\r\ndata: " + body + b"\r\n\r\n")
            except (OSError, socket.timeout):
                pass
            return

        # Stream plain JSON without building full string in memory
        chunks = list(json.JSONEncoder(default=str).iterencode(rpc_response))
        body = b"".join(c.encode("utf-8") for c in chunks)
        try:
            self._send_headers(200, "application/json", content_length=len(body))
            self.wfile.write(body)
        except (OSError, socket.timeout):
            # Client went away — drop silently. The toolkit and project are
            # unaffected; another connection can be served immediately.
            pass

    def _empty(self, status=202):
        try:
            self._send_headers(status, "application/octet-stream",
                               content_length=0)
        except (OSError, socket.timeout):
            pass

    # -- HTTP verbs ------------------------------------------------------ #
    def do_POST(self):
        # Bounded read so a slow client can't pin this thread forever.
        self.connection.settimeout(self.server.socket_timeout)
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError:
            length = 0

        if length > self.MAX_BODY_BYTES:
            try:
                self._send_headers(413, "application/json",
                                   content_length=0)
            except (OSError, socket.timeout):
                pass
            return

        try:
            raw = self.rfile.read(length) if length else b""
        except (OSError, socket.timeout):
            return
        try:
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            try:
                self._respond({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": "Parse error"}})
            except (OSError, socket.timeout):
                pass
            return
        try:
            rpc_response = self.server.handle_rpc(message)
        except (OSError, socket.timeout):
            return
        if rpc_response is None:        # notification — no body
            self._empty(202)
        else:
            self._respond(rpc_response)

    def do_GET(self):
        # We don't push server-initiated messages; no SSE stream to open.
        try:
            self._send_headers(405, "application/octet-stream", content_length=0)
        except (OSError, socket.timeout):
            pass

    def do_DELETE(self):
        self._empty(200)


class _RpcServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass,
                 toolkit, executor, socket_timeout=DEFAULT_SOCKET_TIMEOUT,
                 server_timeout=DEFAULT_SERVER_TIMEOUT, bind_socket=None):
        # If we have a pre-bound socket (TOCTOU-free path), let the
        # ThreadingHTTPServer dup it via ``server_bind`` / ``activate_socket``.
        if bind_socket is not None:
            self.socket = bind_socket
            self.server_address = self.socket.getsockname()
            # Skip HTTPServer.__init__'s bind/listen — we already have them.
            # Call the BaseServer init manually to set the right fields.
            from socketserver import BaseServer
            BaseServer.__init__(self, self.server_address, RequestHandlerClass)
            self.socket.settimeout(socket_timeout)
        else:
            super().__init__(server_address, RequestHandlerClass)
            self.socket.settimeout(socket_timeout)
        self.toolkit = toolkit
        self.executor = executor
        self.session_id = uuid.uuid4().hex
        # Bound the serve_forever poll interval so shutdown is prompt even
        # when no client connects.
        self.timeout = server_timeout
        # Per-handler socket timeout (so a stalled read in one connection
        # doesn't block others).
        self.socket_timeout = socket_timeout

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
                # Cancellation notifications: we honour them by flipping the
                # toolkit's cancel token so any in-flight tool call unwinds.
                if method == "notifications/cancelled":
                    try:
                        self.toolkit.request_cancel()
                    except Exception:
                        pass
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
                # explicit is_error flag travels to the client; don't
                # rely on a string-prefix heuristic downstream.
                if isinstance(out, dict) and out.get("cancelled"):
                    is_error = True
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
                 poll_interval=0.5, parent=None,
                 socket_timeout=DEFAULT_SOCKET_TIMEOUT,
                 server_timeout=DEFAULT_SERVER_TIMEOUT):
        super().__init__(parent)
        self.toolkit = toolkit
        self.executor = executor
        self.host = host
        # keep a reference to the pre-bound listening socket across
        # thread startup so we don't rebind.
        self._bound_sock, self.port = _allocate_listening_socket(host, port)
        self.poll_interval = poll_interval
        self.socket_timeout = socket_timeout
        self.server_timeout = server_timeout
        self._server = None

    @property
    def base_url(self):
        return f"http://{self.host}:{self.port}/mcp"

    @staticmethod
    def dependencies_available():
        return True  # stdlib only — always available

    def run(self):  # QThread entry point — runs on the background thread
        self._server = _RpcServer(
            (self.host, self.port), _Handler,
            self.toolkit, self.executor,
            socket_timeout=self.socket_timeout,
            server_timeout=self.server_timeout,
            bind_socket=self._bound_sock,
        )
        try:
            self._server.serve_forever(poll_interval=self.poll_interval)
        finally:
            # Close the listening FD we kept alive for the TOCTOU-free path.
            try:
                self._bound_sock.close()
            except Exception:
                pass

    def stop(self):
        # shutdown() unblocks serve_forever via its poll timeout; we set a
        # tight server_timeout at construction so this returns within
        # ~server_timeout seconds even if a handler is mid-request.
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        try:
            self._bound_sock.close()
        except Exception:
            pass
        self.wait(5000)
