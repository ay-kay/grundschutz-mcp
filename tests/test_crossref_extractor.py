"""Tests for the cross-reference extractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from grundschutz_mcp.db import apply_schema, connect
from grundschutz_mcp.ingest.crossref_extractor import extract_cross_references
from grundschutz_mcp.ingest.xml_importer import import_xml

REAL_XML = Path(__file__).resolve().parents[1] / "XML_Kompendium_2023.xml"


# ---------------------------------------------------------------------------
# Synthetic-fixture unit tests (use tiny_db from conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def loaded_db(tiny_db):
    """tiny_db with a richer prose so the extractor has something to find."""
    tiny_db.execute(
        "UPDATE requirement SET prose = ? WHERE code = 'APP.1.1.A2'",
        (
            "Diese Anforderung baut auf APP.1.1.A1 auf und ergänzt ORP.1.A1. "
            "Weitere Informationen finden sich in APP.1.1 selbst sowie im "
            "Baustein ORP.1 und in der nicht existierenden APP.99.A1.",
        ),
    )
    tiny_db.execute(
        "UPDATE requirement SET prose = ? WHERE code = 'ORP.1.A1'",
        ("Diese Anforderung verweist auf APP.1.1.A2.",),
    )
    tiny_db.commit()
    return tiny_db


def test_extracts_requirement_and_module_references(loaded_db):
    extract_cross_references(loaded_db)
    rows = loaded_db.execute(
        "SELECT cr.target_code, cr.target_kind FROM cross_reference cr "
        "JOIN requirement r ON r.id = cr.source_req_id "
        "WHERE r.code = 'APP.1.1.A2' "
        "ORDER BY cr.target_code"
    ).fetchall()
    targets = {(r["target_code"], r["target_kind"]) for r in rows}
    # APP.1.1 is filtered (own module). APP.99.A1 is unknown but kept.
    assert ("APP.1.1.A1", "requirement") in targets
    assert ("ORP.1.A1", "requirement") in targets
    assert ("ORP.1", "module") in targets
    assert ("APP.99.A1", "requirement") in targets


def test_skips_self_reference_to_own_module(loaded_db):
    extract_cross_references(loaded_db)
    rows = loaded_db.execute(
        "SELECT cr.target_code FROM cross_reference cr "
        "JOIN requirement r ON r.id = cr.source_req_id "
        "WHERE r.code = 'APP.1.1.A2' AND cr.target_code = 'APP.1.1'"
    ).fetchall()
    assert rows == []


def test_skips_self_reference_to_own_code(loaded_db):
    loaded_db.execute(
        "UPDATE requirement SET prose = ? WHERE code = 'APP.1.1.A2'",
        ("APP.1.1.A2 ist nützlich und APP.1.1.A1 ist auch wichtig.",),
    )
    extract_cross_references(loaded_db)
    targets = {
        r["target_code"]
        for r in loaded_db.execute(
            "SELECT cr.target_code FROM cross_reference cr "
            "JOIN requirement r ON r.id = cr.source_req_id "
            "WHERE r.code = 'APP.1.1.A2'"
        )
    }
    assert "APP.1.1.A2" not in targets
    assert "APP.1.1.A1" in targets


def test_deduplicates_within_a_source(loaded_db):
    loaded_db.execute(
        "UPDATE requirement SET prose = ? WHERE code = 'APP.1.1.A2'",
        ("APP.1.1.A1 wird hier mehrmals erwähnt: APP.1.1.A1, APP.1.1.A1.",),
    )
    extract_cross_references(loaded_db)
    n = loaded_db.execute(
        "SELECT COUNT(*) AS n FROM cross_reference cr "
        "JOIN requirement r ON r.id = cr.source_req_id "
        "WHERE r.code = 'APP.1.1.A2' AND cr.target_code = 'APP.1.1.A1'"
    ).fetchone()["n"]
    assert n == 1


def test_reimport_wipes_old_rows(loaded_db):
    first = extract_cross_references(loaded_db)
    second = extract_cross_references(loaded_db)
    assert first.references_inserted == second.references_inserted
    total = loaded_db.execute("SELECT COUNT(*) AS n FROM cross_reference").fetchone()["n"]
    assert total == second.references_inserted


# ---------------------------------------------------------------------------
# Smoketest against real catalog (skipped when XML missing)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not REAL_XML.is_file(),
    reason="XML_Kompendium_2023.xml missing - run `make fetch-xml` to enable.",
)
def test_smoketest_against_real_data(tmp_path: Path):
    conn = connect(tmp_path / "real.db")
    apply_schema(conn)
    import_xml(conn, REAL_XML)
    stats = extract_cross_references(conn)

    # The BSI 2023 corpus has few requirement-internal cross-references
    # (most cross-Baustein hints live in module descriptions, which we
    # don't scan). Empirically ~120 references across ~50 modules.
    per_module = conn.execute(
        "SELECT COUNT(DISTINCT m.code) AS n "
        "FROM module m "
        "JOIN requirement r ON r.module_id = m.id "
        "JOIN cross_reference cr ON cr.source_req_id = r.id"
    ).fetchone()["n"]
    assert per_module >= 40
    assert stats.references_inserted >= 100
    assert stats.requirement_targets > 0
    assert stats.module_targets > 0
    conn.close()
