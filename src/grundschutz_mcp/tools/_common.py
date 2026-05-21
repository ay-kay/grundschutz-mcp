"""Shared helpers for tool implementations."""

from __future__ import annotations

import difflib
import sqlite3
from typing import Any

# Entity types accepted by search tools (matches the FTS / vec table prefix).
ENTITY_TYPES = ("requirement", "module", "threat")


class CodeNotFoundError(LookupError):
    """Raised when an entity code is unknown. Carries fuzzy-match suggestions."""

    def __init__(self, code: str, suggestions: list[str]) -> None:
        self.code = code
        self.suggestions = suggestions
        super().__init__(f"Unknown code {code!r}. Did you mean one of: {suggestions}?")

    def as_error_response(self, entity_type: str) -> dict[str, Any]:
        return {
            "error": "not_found",
            "entity_type": entity_type,
            "code": self.code,
            "suggestions": self.suggestions,
            "message": (
                f"No {entity_type} with code {self.code!r}. "
                f"Closest matches: {', '.join(self.suggestions) or '—'}."
            ),
        }


def fuzzy_suggest(
    conn: sqlite3.Connection,
    table: str,
    code: str,
    n: int = 5,
) -> list[str]:
    """Return up to ``n`` close matches for ``code`` against ``table.code``."""
    rows = conn.execute(f"SELECT code FROM {table}").fetchall()
    all_codes = [r["code"] for r in rows]
    return difflib.get_close_matches(code, all_codes, n=n, cutoff=0.4)


def row_to_dict(row: sqlite3.Row, /) -> dict[str, Any]:
    """Convert a Row into a plain dict, dropping any NULLs."""
    out: dict[str, Any] = {}
    for k in row.keys():
        v = row[k]
        if v is not None:
            out[k] = v
    return out


def list_roles_for_requirement(
    conn: sqlite3.Connection,
    requirement_id: int,
) -> list[str]:
    return [
        r["name"]
        for r in conn.execute(
            "SELECT ro.name FROM requirement_role rr "
            "JOIN role ro ON ro.id = rr.role_id "
            "WHERE rr.requirement_id = ? "
            "ORDER BY ro.name",
            (requirement_id,),
        )
    ]
