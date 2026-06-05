"""grundschutz-mcp: MCP server for the BSI IT-Grundschutz-Kompendium."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("grundschutz-mcp")
except PackageNotFoundError:  # not installed (e.g. running from a raw checkout)
    __version__ = "0.0.0+unknown"
