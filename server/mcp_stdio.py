"""Standalone stdio→HTTP MCP proxy for AgenticGIS (QGIS-free, stdlib only).

Most agent CLIs only speak the *stdio* MCP transport, but the AgenticGIS
bridge inside QGIS serves *streamable HTTP*. This tiny proxy bridges the
two: it reads newline-delimited JSON-RPC frames on stdin, forwards each one
to the live bridge with a plain HTTP POST, and writes the JSON-RPC response
back on stdout. Any MCP-capable client can therefore launch it as its
"MCP server":

    claude mcp add agenticgis -s user -- \
        python3 <plugins>/AgenticGIS/server/mcp_stdio.py

With no ``--url`` the proxy reads ``~/.agenticgis/mcp.json`` (written by the
plugin) and probes each registered QGIS instance until one answers an
``initialize`` request — so several QGIS profiles/versions can run at once
and the first live one wins.

Runs on any Python >= 3.9 with zero imports beyond the standard library;
it may be executed directly as a script (python3 mcp_stdio.py) or imported
as part of the package.
"""

import argparse
import json
import os
import sys

try:
    from .discovery import discovery_path, live_servers
except ImportError:  # executed as a loose script: server/ is on sys.path
    from discovery import discovery_path, live_servers  # noqa: E402

PROBE_TIMEOUT = 5.0  # seconds per candidate server probe
DEFAULT_TIMEOUT = 600.0  # seconds for a forwarded request (tools/call can be slow)


class ProxyError(Exception):
    """Raised when no usable bridge can be located."""


def _split_url(url):
    """Return ``(host, port, path)`` for an http:// URL."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        raise ProxyError(f"Unsupported AgenticGIS MCP URL (need http://): {url!r}")
    return parts.hostname, parts.port or 80, parts.path or "/mcp"


def make_post_fn(host, port, path, timeout=DEFAULT_TIMEOUT):
    """Build ``post(body_bytes) -> (status, response_bytes)`` over http.client."""
    import http.client

    def post(body):
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request(
                "POST",
                path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Connection": "close",
                },
            )
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    return post


def _write_frame(stdout, payload):
    stdout.write(json.dumps(payload) + "\n")
    stdout.flush()


def serve(stdin, stdout, post):
    """Run the stdio JSON-RPC loop until stdin ends.

    *post* takes the raw request frame and returns ``(status, body)``.
    Requests are answered with the bridge's response; notifications get no
    output; a bridge that is down is reported as a JSON-RPC error without
    killing the stream (QGIS may come back).
    """
    for line in stdin:
        frame = line.strip()
        if not frame:
            continue
        try:
            message = json.loads(frame)
        except ValueError:
            continue  # malformed frame — never break the stream
        if not isinstance(message, dict):
            continue
        mid = message.get("id")
        if mid is None:
            # Notification: the bridge accepts it with 202/empty; no output.
            try:
                post(frame.encode("utf-8"))
            except OSError:
                pass
            continue
        try:
            status, body = post(frame.encode("utf-8"))
        except OSError as exc:
            _write_frame(stdout, {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32603,
                          "message": f"AgenticGIS bridge unreachable: {exc}"},
            })
            continue
        if status == 200 and body:
            stdout.write(body.decode("utf-8", "replace").strip() + "\n")
            stdout.flush()
        else:
            _write_frame(stdout, {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32000,
                          "message": f"AgenticGIS bridge returned HTTP {status}"},
            })
    return 0


def resolve_url(explicit=None, probe_timeout=PROBE_TIMEOUT):
    """Return the URL of a live bridge: *explicit*, else the first entry in
    the discovery file that answers an ``initialize`` probe."""
    if explicit:
        return explicit
    candidates = []
    for entry in live_servers():
        url = entry.get("url")
        if isinstance(url, str) and url not in candidates:
            candidates.append(url)
    for url in candidates:
        host, port, path = _split_url(url)
        probe = make_post_fn(host, port, path, timeout=probe_timeout)
        try:
            status, _ = probe(json.dumps({
                "jsonrpc": "2.0", "id": "probe", "method": "initialize", "params": {},
            }).encode("utf-8"))
        except OSError:
            continue
        if status == 200:
            return url
    raise ProxyError(
        "No live AgenticGIS MCP server found. Start QGIS with the AgenticGIS "
        f"plugin enabled (discovery file: {discovery_path()})."
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mcp_stdio",
        description="stdio→HTTP MCP proxy for the AgenticGIS QGIS bridge",
    )
    parser.add_argument("--url", help="bridge URL (default: discover via ~/.agenticgis/mcp.json)")
    parser.add_argument("--discovery", help="alternate discovery-file path")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="seconds to wait for one forwarded request (default 600)")
    args = parser.parse_args(argv)

    if args.discovery:
        os.environ["AGENTICGIS_MCP_DISCOVERY"] = args.discovery

    try:
        url = resolve_url(explicit=args.url)
        host, port, path = _split_url(url)
    except ProxyError as exc:
        print(f"mcp_stdio: {exc}", file=sys.stderr)
        return 1

    return serve(sys.stdin, sys.stdout, make_post_fn(host, port, path, timeout=args.timeout))


if __name__ == "__main__":
    sys.exit(main())
