"""SQLite connection helpers and schema bootstrap.

A single source of truth for how the rest of the codebase opens the database.
The XML importer, embedding generator, and MCP tools should go through
``connect()`` so PRAGMA settings stay consistent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "schema.sql"


def connect(db_path: Path, *, load_vec: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with project-wide PRAGMA defaults.

    Set ``load_vec=True`` if the caller needs the ``sqlite-vec`` extension
    (embedding generator, semantic search tool). Loading it requires the
    ``sqlite_vec`` Python package.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if load_vec:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    return conn


def apply_schema(conn: sqlite3.Connection, schema_path: Path | None = None) -> None:
    """Apply schema.sql to ``conn`` (idempotent: CREATE TABLE IF NOT EXISTS-style is up to schema).

    The shipped schema uses bare ``CREATE TABLE`` statements, so applying it twice
    on the same database will raise. Callers that want a fresh build should drop
    the file first or call this on an empty database.
    """
    schema_path = Path(schema_path or DEFAULT_SCHEMA_PATH)
    sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(sql)
