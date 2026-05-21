"""MCP tool implementations.

Each submodule exposes plain Python functions that take a ``sqlite3.Connection``
and return JSON-serialisable dicts/lists. ``server.py`` is responsible for
binding them to MCP tool names.
"""
