"""Tools dealing with layers (Schichten) and modules (Bausteine)."""

from __future__ import annotations

import sqlite3
from typing import Any

from ._common import CodeNotFoundError, fuzzy_suggest


def list_layers(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Return all 10 layers (Bausteinkategorien) with code and title."""
    return [
        {"code": r["code"], "title": r["title"]}
        for r in conn.execute("SELECT code, title FROM layer ORDER BY code")
    ]


def list_modules(
    conn: sqlite3.Connection,
    layer: str | None = None,
    search: str | None = None,
    priority: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Return modules, optionally filtered by layer code, search term, or
    implementation priority class (R1 / R2 / R3).

    ``search`` is matched against module code and title via FTS5.
    ``priority`` filters by ``module.priority_class`` and accepts ``R1``,
    ``R2`` or ``R3``.

    The default ``limit`` (200) covers the whole catalogue (111 modules), so
    by default the list is complete. The response is an envelope:

        {"modules": [...], "total_count": N, "returned_count": M,
         "truncated": bool}

    where ``total_count`` is how many modules match the filters in total and
    ``truncated`` is true when ``limit`` cut the list short - so a caller can
    tell a complete answer from a capped one instead of guessing.
    """
    if priority is not None and priority not in ("R1", "R2", "R3"):
        raise ValueError(f"priority must be R1, R2 or R3, got {priority!r}")
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit!r}")
    if search:
        sql = (
            "SELECT m.code AS code, m.title AS title, "
            "       l.code AS layer_code, l.title AS layer_title, "
            "       m.priority_class AS priority_class "
            "FROM module_fts f "
            "JOIN module m ON m.id = f.rowid "
            "JOIN layer  l ON l.id = m.layer_id "
            "WHERE module_fts MATCH ? "
        )
        args: list[Any] = [search]
        if layer:
            sql += "  AND l.code = ? "
            args.append(layer)
        if priority:
            sql += "  AND m.priority_class = ? "
            args.append(priority)
    else:
        sql = (
            "SELECT m.code AS code, m.title AS title, "
            "       l.code AS layer_code, l.title AS layer_title, "
            "       m.priority_class AS priority_class "
            "FROM module m JOIN layer l ON l.id = m.layer_id "
        )
        args = []
        where: list[str] = []
        if layer:
            where.append("l.code = ?")
            args.append(layer)
        if priority:
            where.append("m.priority_class = ?")
            args.append(priority)
        if where:
            sql += "WHERE " + " AND ".join(where) + " "
        sql += "ORDER BY m.code"
    # The catalogue is tiny (111 modules), so fetching every match and
    # slicing in Python is cheap and lets us report total_count honestly.
    rows = [dict(r) for r in conn.execute(sql, args)]
    modules = rows[:limit]
    return {
        "modules": modules,
        "total_count": len(rows),
        "returned_count": len(modules),
        "truncated": len(rows) > len(modules),
    }


def get_module(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    """Return one module with full description, threat situation, requirements.

    The response includes ``specific_threats``: the baustein-specific named
    threat scenarios that appear as sub-sections of "Gefährdungslage" in the
    BSI XML. These are NOT the catalogue's elementary G 0.x threats - those
    are reached via :func:`grundschutz_mcp.tools.threats.get_threats_for_requirement`.
    """
    row = conn.execute(
        "SELECT m.id AS id, m.code AS code, m.title AS title, "
        "       m.description AS description, m.threat_situation AS threat_situation, "
        "       m.priority_class AS priority_class, "
        "       l.code AS layer_code, l.title AS layer_title, "
        "       ro.name AS responsible_role "
        "FROM module m "
        "JOIN layer l ON l.id = m.layer_id "
        "LEFT JOIN role ro ON ro.id = m.responsible_role_id "
        "WHERE m.code = ?",
        (code,),
    ).fetchone()
    if row is None:
        raise CodeNotFoundError(code, fuzzy_suggest(conn, "module", code))

    reqs = [
        {
            "code": r["code"],
            "title": r["title"],
            "level": r["level"],
            "is_deprecated": bool(r["is_deprecated"]),
        }
        for r in conn.execute(
            "SELECT code, title, level, is_deprecated FROM requirement "
            "WHERE module_id = ? "
            "ORDER BY CASE level "
            "  WHEN 'Basis' THEN 0 WHEN 'Standard' THEN 1 WHEN 'Hoch' THEN 2 END, "
            "  code",
            (row["id"],),
        )
    ]

    specific_threats = list_module_threats_for_id(conn, row["id"])

    return {
        "code": row["code"],
        "title": row["title"],
        "layer_code": row["layer_code"],
        "layer_title": row["layer_title"],
        "responsible_role": row["responsible_role"],
        "priority_class": row["priority_class"],
        "description": row["description"],
        "threat_situation": row["threat_situation"],
        "specific_threats": specific_threats,
        "requirements": reqs,
    }


def list_module_threats(
    conn: sqlite3.Connection,
    code: str,
) -> dict[str, Any]:
    """Return the baustein-specific threat scenarios of one module.

    These are the named sub-sections of the Baustein's "Gefährdungslage" -
    e.g. "Ransomware", "Fehlende Wiederherstellungstests" for CON.3.
    Each entry has ``title``, ``description`` and ``ordering`` (the
    position the BSI assigned in the source text).
    """
    module = conn.execute(
        "SELECT id FROM module WHERE code = ?",
        (code,),
    ).fetchone()
    if module is None:
        raise CodeNotFoundError(code, fuzzy_suggest(conn, "module", code))
    return {
        "module_code": code,
        "specific_threats": list_module_threats_for_id(conn, module["id"]),
    }


def list_module_threats_for_id(
    conn: sqlite3.Connection,
    module_id: int,
) -> list[dict[str, Any]]:
    """Helper: fetch specific threats for a known module id."""
    return [
        {"ordering": r["ordering"], "title": r["title"], "description": r["description"]}
        for r in conn.execute(
            "SELECT ordering, title, description FROM module_specific_threat "
            "WHERE module_id = ? ORDER BY ordering",
            (module_id,),
        )
    ]
