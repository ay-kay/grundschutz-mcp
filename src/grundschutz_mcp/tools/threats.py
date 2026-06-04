"""Tools dealing with elementary threats and their requirement links."""

from __future__ import annotations

import sqlite3
from typing import Any

from ._common import CodeNotFoundError, fuzzy_suggest
from .requirements import protection_goals_for_requirement


def list_threats(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """Return all 47 elementary threats (G 0.x)."""
    return [
        {"code": r["code"], "title": r["title"]}
        for r in conn.execute(
            "SELECT code, title FROM threat "
            # Numeric sort on the trailing number for natural ordering.
            # Codes look like "G 0.14"; the number starts right after the dot,
            # so anchor on INSTR rather than a fixed offset (SUBSTR(code, 4)
            # would yield ".14", which CASTs to 0 for every row).
            "ORDER BY CAST(SUBSTR(code, INSTR(code, '.') + 1) AS INTEGER)"
        )
    ]


def get_threat(conn: sqlite3.Connection, code: str) -> dict[str, Any]:
    """Return one threat with its full description."""
    row = conn.execute(
        "SELECT code, title, description FROM threat WHERE code = ?",
        (code,),
    ).fetchone()
    if row is None:
        raise CodeNotFoundError(code, fuzzy_suggest(conn, "threat", code))
    return {
        "code": row["code"],
        "title": row["title"],
        "description": row["description"],
    }


def get_threats_for_requirement(
    conn: sqlite3.Connection,
    req_code: str,
) -> dict[str, Any]:
    """Return the threats a requirement addresses, plus its protection goals.

    ``protection_goals`` (C/I/A) is an attribute of the *requirement* as a
    whole, taken from the BSI Kreuzreferenztabelle's Grundwerte column - not
    of each individual threat link. The BSI assigns it to only a subset of
    requirements, so an empty list means BSI assigned none, not that data is
    missing. It is reported once, at the top level, rather than repeated on
    every threat.
    """
    req = conn.execute(
        "SELECT id FROM requirement WHERE code = ?",
        (req_code,),
    ).fetchone()
    if req is None:
        raise CodeNotFoundError(req_code, fuzzy_suggest(conn, "requirement", req_code))

    threats = [
        {"code": r["code"], "title": r["title"]}
        for r in conn.execute(
            "SELECT t.code AS code, t.title AS title "
            "FROM requirement_threat rt "
            "JOIN threat t ON t.id = rt.threat_id "
            "WHERE rt.requirement_id = ? "
            # Numeric sort on the trailing number (see list_threats for why
            # we anchor on the dot instead of a fixed SUBSTR offset).
            "ORDER BY CAST(SUBSTR(t.code, INSTR(t.code, '.') + 1) AS INTEGER)",
            (req["id"],),
        )
    ]

    return {
        "requirement_code": req_code,
        "protection_goals": protection_goals_for_requirement(conn, req["id"]),
        "threats": threats,
    }


def get_requirements_for_threat(
    conn: sqlite3.Connection,
    threat_code: str,
) -> dict[str, Any]:
    """Return the requirements that address a given threat."""
    threat = conn.execute(
        "SELECT id FROM threat WHERE code = ?",
        (threat_code,),
    ).fetchone()
    if threat is None:
        raise CodeNotFoundError(threat_code, fuzzy_suggest(conn, "threat", threat_code))

    rows = conn.execute(
        "SELECT r.code AS code, r.title AS title, r.level AS level, "
        "       m.code AS module_code "
        "FROM requirement_threat rt "
        "JOIN requirement r ON r.id = rt.requirement_id "
        "JOIN module      m ON m.id = r.module_id "
        "WHERE rt.threat_id = ? "
        "ORDER BY r.code",
        (threat["id"],),
    )
    return {
        "threat_code": threat_code,
        "requirements": [dict(r) for r in rows],
    }
