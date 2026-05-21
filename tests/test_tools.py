"""Tests covering the MCP-facing tools.

Each tool gets at least one smoketest against the tiny synthetic
fixture from ``conftest.py``. Real-data smoketests for the search
tools live in test_fts.py / test_embeddings.py / test_e2e.py.
"""

from __future__ import annotations

import pytest

from grundschutz_mcp.ingest.crossref_extractor import extract_cross_references
from grundschutz_mcp.tools import modules as mod_tools
from grundschutz_mcp.tools import requirements as req_tools
from grundschutz_mcp.tools import search as search_tools
from grundschutz_mcp.tools import threats as threat_tools
from grundschutz_mcp.tools._common import CodeNotFoundError


@pytest.fixture
def loaded_db(tiny_db):
    """Tiny seeded DB with cross-references extracted from the prose."""
    extract_cross_references(tiny_db)
    return tiny_db


# ---------------------------------------------------------------------------
# modules.py
# ---------------------------------------------------------------------------


def test_list_layers(loaded_db):
    res = mod_tools.list_layers(loaded_db)
    assert {"code": "APP", "title": "Anwendungen"} in res
    assert {"code": "ORP", "title": "Organisation und Personal"} in res


def test_list_modules_unfiltered(loaded_db):
    res = mod_tools.list_modules(loaded_db)
    codes = [m["code"] for m in res]
    assert "APP.1.1" in codes and "ORP.1" in codes


def test_list_modules_by_layer(loaded_db):
    res = mod_tools.list_modules(loaded_db, layer="APP")
    assert all(m["layer_code"] == "APP" for m in res)


def test_list_modules_search(loaded_db):
    res = mod_tools.list_modules(loaded_db, search="Office")
    assert any(m["code"] == "APP.1.1" for m in res)


def test_list_modules_filter_by_priority(loaded_db):
    # Manually set priority classes since the fixture XML doesn't include them.
    loaded_db.execute("UPDATE module SET priority_class = 'R1' WHERE code = 'APP.1.1'")
    loaded_db.execute("UPDATE module SET priority_class = 'R3' WHERE code = 'ORP.1'")
    r1 = mod_tools.list_modules(loaded_db, priority="R1")
    codes = [m["code"] for m in r1]
    assert codes == ["APP.1.1"]
    r3 = mod_tools.list_modules(loaded_db, priority="R3")
    codes = [m["code"] for m in r3]
    assert codes == ["ORP.1"]


def test_list_modules_invalid_priority(loaded_db):
    with pytest.raises(ValueError, match="priority"):
        mod_tools.list_modules(loaded_db, priority="R9")


def test_get_module(loaded_db):
    res = mod_tools.get_module(loaded_db, "APP.1.1")
    assert res["title"] == "Office-Produkte"
    assert res["layer_code"] == "APP"
    assert res["responsible_role"] == "IT-Betrieb"
    assert "Office-Produkte schützen" in res["description"]
    codes = [r["code"] for r in res["requirements"]]
    assert "APP.1.1.A2" in codes
    # specific_threats embedded directly into the module payload
    titles = [t["title"] for t in res["specific_threats"]]
    assert "Schadsoftware in Office-Dokumenten" in titles
    assert "Datenverlust durch Fehlbedienung" in titles


def test_list_module_threats(loaded_db):
    res = mod_tools.list_module_threats(loaded_db, "APP.1.1")
    assert res["module_code"] == "APP.1.1"
    assert [t["ordering"] for t in res["specific_threats"]] == [1, 2]
    assert res["specific_threats"][0]["title"] == "Schadsoftware in Office-Dokumenten"
    assert res["specific_threats"][0]["description"].startswith("Makros")


def test_list_module_threats_unknown_module(loaded_db):
    with pytest.raises(CodeNotFoundError):
        mod_tools.list_module_threats(loaded_db, "APP.X.Y")


def test_get_module_unknown_suggests(loaded_db):
    with pytest.raises(CodeNotFoundError) as exc:
        mod_tools.get_module(loaded_db, "APP.1.X")
    assert exc.value.suggestions  # non-empty
    payload = exc.value.as_error_response("module")
    assert payload["error"] == "not_found"


# ---------------------------------------------------------------------------
# requirements.py
# ---------------------------------------------------------------------------


def test_list_requirements_excludes_deprecated_by_default(loaded_db):
    res = req_tools.list_requirements(loaded_db, "APP.1.1")
    codes = [r["code"] for r in res]
    assert "APP.1.1.A2" in codes
    assert "APP.1.1.A1" not in codes  # ENTFALLEN, excluded


def test_list_requirements_include_deprecated(loaded_db):
    res = req_tools.list_requirements(loaded_db, "APP.1.1", include_deprecated=True)
    codes = [r["code"] for r in res]
    assert "APP.1.1.A1" in codes


def test_list_requirements_by_level(loaded_db):
    res = req_tools.list_requirements(loaded_db, "APP.1.1", level="Basis")
    assert all(r["level"] == "Basis" for r in res)


def test_get_requirement(loaded_db):
    res = req_tools.get_requirement(loaded_db, "APP.1.1.A2")
    assert res["title"] == "Einschränken von Aktiven Inhalten"
    assert res["level"] == "Basis"
    assert "Aktive Inhalte" in res["prose"]
    assert res["module_code"] == "APP.1.1"
    assert "IT-Betrieb" in res["roles"]
    assert "Benutzende" in res["roles"]


def test_get_cross_references(loaded_db):
    res = req_tools.get_cross_references(loaded_db, "APP.1.1.A2")
    target_codes = [r["target_code"] for r in res["references"]]
    assert "APP.1.1.A1" in target_codes  # requirement
    assert "ORP.1" in target_codes  # module


# ---------------------------------------------------------------------------
# threats.py
# ---------------------------------------------------------------------------


def test_list_threats(loaded_db):
    res = threat_tools.list_threats(loaded_db)
    codes = [t["code"] for t in res]
    assert "G 0.14" in codes


def test_get_threat(loaded_db):
    res = threat_tools.get_threat(loaded_db, "G 0.14")
    assert res["title"].startswith("Ausspähen")
    assert "Spionage-Beschreibung" in res["description"]


def test_get_threats_for_requirement(loaded_db):
    res = threat_tools.get_threats_for_requirement(loaded_db, "ORP.1.A1")
    threats = res["threats"]
    # The fixture links ORP.1.A1 to G 0.14 with goals [C, I].
    codes = [t["code"] for t in threats]
    assert "G 0.14" in codes
    g14 = next(t for t in threats if t["code"] == "G 0.14")
    assert set(g14["protection_goals"]) == {"C", "I"}


def test_get_requirements_for_threat(loaded_db):
    res = threat_tools.get_requirements_for_threat(loaded_db, "G 0.14")
    codes = [r["code"] for r in res["requirements"]]
    assert "ORP.1.A1" in codes


# ---------------------------------------------------------------------------
# search.py - keyword (FTS)
# ---------------------------------------------------------------------------


def test_search_keyword(loaded_db):
    res = search_tools.search_keyword(loaded_db, "Aktive")
    codes = [h["code"] for h in res["hits"]]
    assert "APP.1.1.A2" in codes
    # Each hit must carry an entity_type and score.
    for h in res["hits"]:
        assert h["entity_type"] in ("requirement", "module", "threat")
        assert "score" in h


def test_search_keyword_filter_entity_types(loaded_db):
    res = search_tools.search_keyword(loaded_db, "Office", entity_types=["module"])
    assert all(h["entity_type"] == "module" for h in res["hits"])


def test_search_keyword_unknown_entity_type_raises(loaded_db):
    with pytest.raises(ValueError, match="unknown entity_types"):
        search_tools.search_keyword(loaded_db, "x", entity_types=["xx"])


def test_search_keyword_empty_query(loaded_db):
    res = search_tools.search_keyword(loaded_db, "")
    assert res["hits"] == []


# ---------------------------------------------------------------------------
# search.py - hybrid (RRF)
# ---------------------------------------------------------------------------


def test_search_hybrid_returns_keyword_hits_without_embeddings(loaded_db):
    # No embeddings have been generated for the tiny fixture; hybrid must
    # still produce results via the keyword side only.
    res = search_tools.search_hybrid(loaded_db, "Aktive")
    codes = [h["code"] for h in res["hits"]]
    assert "APP.1.1.A2" in codes
    # Note on graceful degradation must be present.
    assert "note" in res
    assert "Semantic search unavailable" in res["note"]
    # Each hit should report which retriever found it.
    h = next(h for h in res["hits"] if h["code"] == "APP.1.1.A2")
    assert h["keyword_rank"] is not None
    assert h["semantic_rank"] is None
    assert h["rrf_score"] > 0


def test_search_hybrid_or_tokenization_handles_phrased_queries(loaded_db):
    # FTS5 would default to implicit AND across tokens and miss this if any
    # token were absent. Hybrid uses OR-of-tokens, so we get hits.
    res = search_tools.search_hybrid(
        loaded_db,
        "Wie schütze ich gegen Aktive Inhalte und Schadcode?",
    )
    codes = [h["code"] for h in res["hits"]]
    assert "APP.1.1.A2" in codes


def test_search_hybrid_filters_by_entity_type(loaded_db):
    res = search_tools.search_hybrid(loaded_db, "Office", entity_types=["module"])
    assert all(h["entity_type"] == "module" for h in res["hits"])


def test_search_hybrid_empty_query(loaded_db):
    assert search_tools.search_hybrid(loaded_db, "")["hits"] == []


def test_search_keyword_module_promotes_specific_threat_hits(loaded_db):
    # The fixture seeded APP.1.1 with a "Schadsoftware in Office-Dokumenten"
    # specific threat. FTS over the module table alone wouldn't find it,
    # but search_keyword now also looks at module_specific_threat_fts and
    # surfaces the parent module.
    res = search_tools.search_keyword(
        loaded_db,
        "Schadsoftware",
        entity_types=["module"],
        limit=5,
    )
    codes = [h["code"] for h in res["hits"]]
    assert "APP.1.1" in codes


def test_to_or_query_strips_stopwords_and_punctuation():
    from grundschutz_mcp.tools.search import _to_or_query

    out = _to_or_query("Was schützt vor Ransomware?")
    # German stopwords (was, vor) dropped, punctuation gone, OR-joined.
    assert out == '"schützt" OR "Ransomware"'


def test_to_or_query_escapes_fts_operators():
    from grundschutz_mcp.tools.search import _to_or_query

    out = _to_or_query("NEAR OR Backup")
    # OR/NEAR collide with FTS5 operators -> dropped as stopwords;
    # remaining token quoted to make it a literal phrase.
    assert out == '"Backup"'


# ---------------------------------------------------------------------------
# search.py - semantic (skipped, needs embeddings - covered in embedding tests
# and end-to-end smoketest separately).
# ---------------------------------------------------------------------------
