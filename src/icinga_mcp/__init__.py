"""MCP server for analysing Icinga 2 monitoring."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("domon-mcp-server")
except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
