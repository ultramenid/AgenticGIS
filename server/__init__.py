"""Local MCP bridge that exposes the QGIS toolkit to external CLI agents."""

try:
    from .mcp_server import McpBridgeServer
except ImportError:  # outside QGIS: discovery.py / mcp_stdio.py stay importable
    McpBridgeServer = None

__all__ = ["McpBridgeServer"]
