"""Extract cross-references between requirements from their prose.

Step 4 of the ingest pipeline. Walks every requirement's ``prose`` column,
finds embedded codes like ``APP.1.1.A2`` (requirement) or ``APP.1.1`` (module),
and writes them to the ``cross_reference`` table with ``target_kind`` set
accordingly.

Rules:
* Self-references are skipped (a requirement citing its own code).
* Longer matches win: ``APP.1.1.A2`` is preferred over ``APP.1.1`` for the
  same text span.
* The target is stored as TEXT; the schema deliberately allows targets that
  no longer exist in the catalog (deprecated/renamed codes). We do, however,
  use the live catalog to decide between target_kind=requirement and
  target_kind=module - if the code matches neither, we still record it as
  ``requirement`` (the more specific guess) and continue.
* Idempotent: existing rows for this catalog are wiped at the start.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

import structlog

log = structlog.get_logger(__name__)

# Requirement codes always end in ".A<digits>". Modules look like "APP.1.1"
# without that suffix. We anchor on word boundaries so we don't split words.
RE_REQUIREMENT_CODE = re.compile(r"\b[A-Z]+(?:\.\d+)+\.A\d+\b")
RE_MODULE_CODE = re.compile(r"\b[A-Z]+(?:\.\d+)+\b(?!\.A\d)")


@dataclass
class CrossRefStats:
    sources_scanned: int = 0
    references_inserted: int = 0
    requirement_targets: int = 0
    module_targets: int = 0
    self_references_skipped: int = 0
    unknown_targets: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def extract_cross_references(
    conn: sqlite3.Connection,
    catalog_name: str = "BSI IT-Grundschutz 200-2",
    edition: str = "2023",
) -> CrossRefStats:
    """Extract cross-references from every requirement's prose into the DB."""
    catalog_row = conn.execute(
        "SELECT id FROM catalog WHERE name = ? AND edition = ?",
        (catalog_name, edition),
    ).fetchone()
    if catalog_row is None:
        raise RuntimeError(f"No catalog row for name={catalog_name!r} edition={edition!r}")
    catalog_id = catalog_row["id"]

    stats = CrossRefStats()
    try:
        with conn:
            req_codes = {
                r["code"]
                for r in conn.execute(
                    "SELECT code FROM requirement WHERE catalog_id = ?",
                    (catalog_id,),
                )
            }
            module_codes = {
                r["code"]
                for r in conn.execute(
                    "SELECT code FROM module WHERE catalog_id = ?",
                    (catalog_id,),
                )
            }

            # Wipe existing cross-references for this catalog's requirements.
            conn.execute(
                "DELETE FROM cross_reference WHERE source_req_id IN ("
                "  SELECT id FROM requirement WHERE catalog_id = ?"
                ")",
                (catalog_id,),
            )

            for source in conn.execute(
                "SELECT id, code, prose FROM requirement "
                "WHERE catalog_id = ? AND prose IS NOT NULL",
                (catalog_id,),
            ):
                stats.sources_scanned += 1
                _process_source(
                    conn=conn,
                    source_id=source["id"],
                    source_code=source["code"],
                    prose=source["prose"],
                    req_codes=req_codes,
                    module_codes=module_codes,
                    stats=stats,
                )
    except Exception:
        log.exception("crossref_extraction_failed")
        raise

    log.info("crossref_extraction_done", **stats.as_dict())
    return stats


def _process_source(
    *,
    conn: sqlite3.Connection,
    source_id: int,
    source_code: str,
    prose: str,
    req_codes: set[str],
    module_codes: set[str],
    stats: CrossRefStats,
) -> None:
    """Find every code reference in ``prose`` and insert it once."""
    # Pass 1: requirement codes. These take priority over module codes in
    # the same span (because the requirement regex is more specific).
    seen_codes: set[str] = set()
    requirement_spans: list[tuple[int, int]] = []
    for m in RE_REQUIREMENT_CODE.finditer(prose):
        code = m.group(0)
        requirement_spans.append((m.start(), m.end()))
        if code == source_code:
            stats.self_references_skipped += 1
            continue
        if code in seen_codes:
            continue
        seen_codes.add(code)
        kind = "requirement"
        if code not in req_codes:
            # Code shape says requirement but it's not in the live catalog.
            # Still keep it as requirement - the schema allows TEXT targets.
            stats.unknown_targets += 1
        else:
            stats.requirement_targets += 1
        _insert(conn, source_id, code, kind, stats)

    # Pass 2: module codes. Skip anything that overlapped a requirement match.
    for m in RE_MODULE_CODE.finditer(prose):
        if _overlaps(m.start(), m.end(), requirement_spans):
            continue
        code = m.group(0)
        if code == source_code.rsplit(".A", 1)[0]:
            # Reference to the module the source requirement lives in.
            stats.self_references_skipped += 1
            continue
        if code in seen_codes:
            continue
        if code not in module_codes:
            # Looks module-ish but isn't a real module. Skip to avoid
            # polluting the table with version numbers or other matches.
            continue
        seen_codes.add(code)
        stats.module_targets += 1
        _insert(conn, source_id, code, "module", stats)


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s < end and start < e for s, e in spans)


def _insert(
    conn: sqlite3.Connection,
    source_id: int,
    target_code: str,
    target_kind: str,
    stats: CrossRefStats,
) -> None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO cross_reference "
        "(source_req_id, target_code, target_kind) VALUES (?, ?, ?)",
        (source_id, target_code, target_kind),
    )
    if cur.rowcount > 0:
        stats.references_inserted += 1
