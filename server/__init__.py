"""Local MCP bridge that exposes the QGIS toolkit to external CLI agents."""

from .mcp_server import McpBridgeServer

__all__ = ["McpBridgeServer"]
