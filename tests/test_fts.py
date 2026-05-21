"""FTS5 smoketests against the real catalog.

The FTS tables are populated automatically by triggers when the ingest
importers write into the source tables. These tests verify that the
configured tokenizer (unicode61 + remove_diacritics 2) actually retrieves
the requirements/modules/threats users would expect for common queries
called out in the briefing DoD: "Backup", "Protokollierung",
"Verschlüsselung".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grundschutz_mcp.db import apply_schema, connect
from grundschutz_mcp.ingest.xml_importer import import_xml

REAL_XML = Path(__file__).resolve().parents[1] / "XML_Kompendium_2023.xml"

pytestmark = pytest.mark.skipif(
    not REAL_XML.is_file(),
    reason="XML_Kompendium_2023.xml missing - run `make fetch-xml` to enable.",
)


@pytest.fixture(scope="module")
def real_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fts")
    conn = connect(tmp / "fts.db")
    apply_schema(conn)
    import_xml(conn, REAL_XML)
    yield conn
    conn.close()


def _fts_search(conn, table: str, query: str, limit: int = 25):
    rows = conn.execute(
        f"SELECT code FROM {table} WHERE {table} MATCH ? LIMIT ?",
        (query, limit),
    ).fetchall()
    return [r["code"] for r in rows]


# ---------------------------------------------------------------------------
# Briefing-DoD queries
# ---------------------------------------------------------------------------


def test_keyword_backup_returns_requirements(real_db):
    # "Backup" as a literal word: the canonical Baustein title in German is
    # "Datensicherung", so we don't expect CON.3 itself, but plenty of
    # requirements use the word "Backup" in their prose.
    hits = _fts_search(real_db, "requirement_fts", "Backup")
    assert len(hits) > 5


def test_module_fts_finds_canonical_backup_module(real_db):
    hits = _fts_search(real_db, "module_fts", "Datensicherung")
    assert "CON.3" in hits


def test_keyword_protokollierung_finds_canonical_module(real_db):
    # The canonical Baustein is OPS.1.1.5 "Protokollierung" - that one MUST
    # appear in the module FTS, the requirement FTS picks up anything that
    # uses the term in prose.
    module_hits = _fts_search(real_db, "module_fts", "Protokollierung")
    assert "OPS.1.1.5" in module_hits
    req_hits = _fts_search(real_db, "requirement_fts", "Protokollierung")
    assert len(req_hits) > 5


def test_keyword_verschluesselung_diacritic_folding(real_db):
    # Search without umlaut should still find "Verschlüsselung" content.
    hits = _fts_search(real_db, "requirement_fts", "Verschlusselung")
    assert len(hits) > 5, f"diacritic-folded query yielded {len(hits)} hits"


def test_threat_fts_finds_threats(real_db):
    hits = _fts_search(real_db, "threat_fts", "Spionage")
    # "Ausspähen von Informationen (Spionage)" is G 0.14.
    assert "G 0.14" in hits
