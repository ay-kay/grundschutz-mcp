"""Tests for the XML importer.

The XML importer is the catalog's primary ingest: it creates layers,
roles, modules, requirements, threats, baustein-specific threat
scenarios and the R1/R2/R3 priority class directly from the BSI
DocBook XML. (Only the requirement<->threat M:N mapping lives outside
the XML, in the KRT XLSX - see test_krt_importer.py.)

Unit tests use a deliberately small DocBook fragment. The smoketest at
the bottom additionally runs the importer against the real
XML_Kompendium_2023.xml shipped via ``make fetch-xml`` and asserts the
expected catalog dimensions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grundschutz_mcp.db import apply_schema, connect
from grundschutz_mcp.ingest.xml_importer import CATALOG_NAME, import_xml

REAL_XML = Path(__file__).resolve().parents[1] / "XML_Kompendium_2023.xml"


# ---------------------------------------------------------------------------
# Synthetic XML fixture: one Layer, two Bausteine, three Anforderungen,
# two Gefährdungen, a Modellierungs-table for R1/R2/R3.
# ---------------------------------------------------------------------------

SYNTHETIC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<book xmlns="http://docbook.org/ns/docbook" version="5.0">
  <chapter>
    <title>Schichtenmodell und Modellierung</title>
    <para>R1 vorrangig, R2 anschließend, R3 zuletzt.</para>
    <informaltable>
      <tr><td><para>R1</para></td><td><para>APP.1.1 Office-Produkte</para></td></tr>
      <tr><td><para>R2</para></td><td><para>ORP.1 Organisation</para></td></tr>
    </informaltable>
  </chapter>

  <chapter>
    <title>Elementare Gefährdungen</title>
    <section>
      <title>G 0.14 Ausspähen von Informationen (Spionage)</title>
      <para>Mit Spionage werden Angriffe bezeichnet ...</para>
    </section>
    <section>
      <title>G 0.39 Schadprogramme</title>
      <para>Schadprogramme werden mit dem Ziel entwickelt ...</para>
    </section>
  </chapter>

  <chapter>
    <title>APP Anwendungen</title>
    <section>
      <title>APP.1.1 Office-Produkte</title>
      <informaltable>
        <tr>
          <td><para>Grundsätzlich zuständig</para></td>
          <td><para>IT-Betrieb</para></td>
        </tr>
        <tr>
          <td><para>Weitere Zuständigkeiten</para></td>
          <td><para>Benutzende</para></td>
        </tr>
      </informaltable>
      <section>
        <title>Beschreibung</title>
        <section>
          <title>Einleitung</title>
          <para>Office-Produkte sind Anwendungen zur Dokumentbearbeitung.</para>
        </section>
        <section>
          <title>Zielsetzung</title>
          <para>Ziel ist der Schutz der verarbeiteten Informationen.</para>
        </section>
      </section>
      <section>
        <title>Gefährdungslage</title>
        <para>Die folgenden Bedrohungen sind für Office-Produkte relevant.</para>
        <section>
          <title>Schadsoftware in Makros</title>
          <para>Makros können Schadcode enthalten.</para>
        </section>
      </section>
      <section>
        <title>Anforderungen</title>
        <section>
          <title>Basis-Anforderungen</title>
          <section>
            <title>APP.1.1.A1 ENTFALLEN (B)</title>
            <para>Diese Anforderung ist entfallen.</para>
          </section>
          <section>
            <title>APP.1.1.A2 Einschränken von Aktiven Inhalten (B) [IT-Betrieb, Benutzende]</title>
            <para>Die Verwendung aktiver Inhalte MUSS beschränkt werden.</para>
          </section>
        </section>
        <section>
          <title>Standard-Anforderungen</title>
          <section>
            <title>APP.1.1.A6 Testen neuer Versionen (S)</title>
            <para>Neue Versionen SOLLTEN getestet werden.</para>
          </section>
        </section>
      </section>
    </section>
  </chapter>

  <chapter>
    <title>ORP Organisation und Personal</title>
    <section>
      <title>ORP.1 Organisation</title>
      <informaltable>
        <tr>
          <td><para>Grundsätzlich zuständig</para></td>
          <td><para>IT-Betrieb</para></td>
        </tr>
      </informaltable>
      <section>
        <title>Anforderungen</title>
        <section>
          <title>Standard-Anforderungen</title>
          <section>
            <title>ORP.1.A1 Sicherheitsleitlinie (S) [IT-Betrieb]</title>
            <para>Die Institution SOLLTE eine Sicherheitsleitlinie erlassen.</para>
          </section>
        </section>
      </section>
    </section>
  </chapter>
</book>
"""


@pytest.fixture
def db(tmp_path: Path):
    conn = connect(tmp_path / "xml.db")
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def synthetic_xml(tmp_path: Path) -> Path:
    p = tmp_path / "kompendium.xml"
    p.write_text(SYNTHETIC_XML, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Synthetic-fixture unit tests
# ---------------------------------------------------------------------------


def test_creates_catalog_row(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    row = db.execute("SELECT name, edition FROM catalog").fetchone()
    assert row["name"] == CATALOG_NAME
    assert row["edition"] == "2023"


def test_imports_layers_from_chapter_titles(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    layers = {r["code"]: r["title"] for r in db.execute("SELECT code, title FROM layer")}
    assert layers == {"APP": "Anwendungen", "ORP": "Organisation und Personal"}


def test_imports_modules_with_layer_and_role(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    rows = db.execute(
        "SELECT m.code, m.title, l.code AS layer_code, "
        "       ro.name AS responsible_role, m.priority_class "
        "FROM module m "
        "JOIN layer l ON l.id = m.layer_id "
        "LEFT JOIN role ro ON ro.id = m.responsible_role_id "
        "ORDER BY m.code"
    ).fetchall()
    assert [dict(r) for r in rows] == [
        {
            "code": "APP.1.1",
            "title": "Office-Produkte",
            "layer_code": "APP",
            "responsible_role": "IT-Betrieb",
            "priority_class": "R1",
        },
        {
            "code": "ORP.1",
            "title": "Organisation",
            "layer_code": "ORP",
            "responsible_role": "IT-Betrieb",
            "priority_class": "R2",
        },
    ]


def test_imports_requirements_with_level_and_deprecated(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    rows = {
        r["code"]: (r["level"], r["is_deprecated"])
        for r in db.execute("SELECT code, level, is_deprecated FROM requirement")
    }
    assert rows == {
        "APP.1.1.A1": ("Basis", 1),
        "APP.1.1.A2": ("Basis", 0),
        "APP.1.1.A6": ("Standard", 0),
        "ORP.1.A1": ("Standard", 0),
    }


def test_requirement_prose_is_full_section_text(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    row = db.execute("SELECT prose FROM requirement WHERE code = 'APP.1.1.A2'").fetchone()
    assert "aktiver Inhalte" in row["prose"]


def test_bracketed_roles_become_requirement_role_links(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    rows = db.execute(
        "SELECT ro.name FROM requirement_role rr "
        "JOIN role ro ON ro.id = rr.role_id "
        "JOIN requirement r ON r.id = rr.requirement_id "
        "WHERE r.code = 'APP.1.1.A2' "
        "ORDER BY ro.name"
    ).fetchall()
    assert [r["name"] for r in rows] == ["Benutzende", "IT-Betrieb"]


def test_requirement_without_role_bracket_inherits_module_role(db, synthetic_xml):
    """APP.1.1.A6 has no brackets in its title -> falls back to module's
    responsible role ('IT-Betrieb')."""
    import_xml(db, synthetic_xml)
    rows = db.execute(
        "SELECT ro.name FROM requirement_role rr "
        "JOIN role ro ON ro.id = rr.role_id "
        "JOIN requirement r ON r.id = rr.requirement_id "
        "WHERE r.code = 'APP.1.1.A6'"
    ).fetchall()
    assert [r["name"] for r in rows] == ["IT-Betrieb"]


def test_module_description_combines_einleitung_and_zielsetzung(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    row = db.execute(
        "SELECT description, threat_situation FROM module WHERE code = 'APP.1.1'"
    ).fetchone()
    assert "Office-Produkte" in row["description"]
    assert "Schutz der verarbeiteten" in row["description"]
    # threat_situation: intro only, not the named sub-sections
    assert row["threat_situation"] is not None
    assert "Bedrohungen sind für Office" in row["threat_situation"]
    assert "Schadsoftware in Makros" not in row["threat_situation"]


def test_module_specific_threats_extracted(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    rows = db.execute(
        "SELECT mst.ordering, mst.title, mst.description "
        "FROM module_specific_threat mst "
        "JOIN module m ON m.id = mst.module_id "
        "WHERE m.code = 'APP.1.1' ORDER BY mst.ordering"
    ).fetchall()
    assert [(r["ordering"], r["title"]) for r in rows] == [
        (1, "Schadsoftware in Makros"),
    ]
    assert rows[0]["description"].startswith("Makros können Schadcode")


def test_threats_imported_with_description(db, synthetic_xml):
    import_xml(db, synthetic_xml)
    rows = {r["code"]: r["description"] for r in db.execute("SELECT code, description FROM threat")}
    assert "G 0.14" in rows
    assert "G 0.39" in rows
    assert "Spionage" in rows["G 0.14"]


def test_reimport_is_idempotent(db, synthetic_xml):
    first = import_xml(db, synthetic_xml)
    second = import_xml(db, synthetic_xml)
    assert first.as_dict() == second.as_dict()
    assert db.execute("SELECT COUNT(*) FROM catalog").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM requirement").fetchone()[0] == 4


# ---------------------------------------------------------------------------
# Smoketest against the real XML
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not REAL_XML.is_file(),
    reason="XML_Kompendium_2023.xml missing - run `make fetch-xml` to enable.",
)
def test_smoketest_against_real_xml(db):
    stats = import_xml(db, REAL_XML)
    # Hard counts for the 2023 catalog.
    assert stats.layers == 10
    assert stats.modules == 111
    assert stats.requirements == 2125
    assert stats.threats == 47
    assert stats.module_specific_threats > 500
    assert stats.priority_classes_assigned == 111
    assert stats.requirements_missing_level == 0
    # Every Anforderung has at least one role (explicit or via the
    # responsible-role fallback).
    orphans = db.execute(
        "SELECT COUNT(*) FROM requirement r "
        "WHERE NOT EXISTS (SELECT 1 FROM requirement_role rr "
        "                  WHERE rr.requirement_id = r.id)"
    ).fetchone()[0]
    assert orphans == 0
    # CON.3 spot-check
    rows = db.execute(
        "SELECT mst.title FROM module_specific_threat mst "
        "JOIN module m ON m.id = mst.module_id "
        "WHERE m.code = 'CON.3' ORDER BY mst.ordering"
    ).fetchall()
    titles = [r["title"] for r in rows]
    assert "Ransomware" in titles
    # OPS.2.3.A22 typo workaround
    assert db.execute("SELECT 1 FROM requirement WHERE code = 'OPS.2.3.A22'").fetchone() is not None
