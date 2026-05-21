"""Tools dealing with single requirements and their cross-references."""

from __future__ import annotations

import sqlite3
from typing import Any

from ._common import (
    CodeNotFoundError,
    fuzzy_suggest,
    list_roles_for_requirement,
)


def list_requirements(
    conn: sqlite3.Connection,
    module_code: str,
    level: str | None = None,
    include_deprecated: bool = False,
) -> list[dict[str, Any]]:
    """Return the requirements of one module, optionally filtered by level."""
    module = conn.execute(
        "SELECT id FROM module WHERE code = ?",
        (module_code,),
    ).fetchone()
    if module is None:
        raise CodeNotFoundError(module_code, fuzzy_suggest(conn, "module", module_code))

    sql = "SELECT code, title, level, is_deprecated FROM requirement WHERE module_id = ? "
    args: list[Any] = [module["id"]]
    if level is not None:
        if level not in ("Basis", "Standard", "Hoch"):
            raise ValueError(f"level must be Basis|Standard|Hoch, got {level!r}")
        sql += "  AND level = ? "
        args.append(level)
    if not include_deprecated:
        sql += "  AND is_deprecated = 0 "
    sql += (
        "ORDER BY CASE level "
        "  WHEN 'Basis' THEN 0 WHEN 'Standard' THEN 1 WHEN 'Hoch' THEN 2 END, "
        "  code"
    )
    return [
        {
            "code": r["code"],
            "title": r["title"],
            "level": r["level"],
            "is_deprecated": bool(r["is_deprecated"]),
        }
        for r in conn.execute(sql, args)
    ]


def get_requirement(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    """Return one requirement's full prose plus module/role context."""
    row = conn.execute(
        "SELECT r.id AS id, r.code AS code, r.title AS title, r.level AS level, "
        "       r.prose AS prose, r.is_deprecated AS is_deprecated, "
        "       m.code AS module_code, m.title AS module_title, "
        "       l.code AS layer_code "
        "FROM requirement r "
        "JOIN module m ON m.id = r.module_id "
        "JOIN layer  l ON l.id = m.layer_id "
        "WHERE r.code = ?",
        (code,),
    ).fetchone()
    if row is None:
        raise CodeNotFoundError(code, fuzzy_suggest(conn, "requirement", code))

    roles = list_roles_for_requirement(conn, row["id"])
    return {
        "code": row["code"],
        "title": row["title"],
        "level": row["level"],
        "prose": row["prose"],
        "is_deprecated": bool(row["is_deprecated"]),
        "module_code": row["module_code"],
        "module_title": row["module_title"],
        "layer_code": row["layer_code"],
        "roles": roles,
    }


def get_cross_references(
    conn: sqlite3.Connection,
    code: str,
) -> dict[str, Any]:
    """Return the cross-references emitted from one requirement's prose."""
    req = conn.execute(
        "SELECT id FROM requirement WHERE code = ?",
        (code,),
    ).fetchone()
    if req is None:
        raise CodeNotFoundError(code, fuzzy_suggest(conn, "requirement", code))

    refs = [
        {"target_code": r["target_code"], "target_kind": r["target_kind"]}
        for r in conn.execute(
            "SELECT target_code, target_kind FROM cross_reference "
            "WHERE source_req_id = ? "
            "ORDER BY target_kind, target_code",
            (req["id"],),
        )
    ]
    return {"code": code, "references": refs}
