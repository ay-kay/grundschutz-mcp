"""Import the full catalog (structure + prose) directly from the BSI DocBook XML.

This is the single source of truth for everything *except* the Anforderung↔
Gefährdung mapping (which lives in the KRT XLSX and is imported by
:mod:`grundschutz_mcp.ingest.krt_importer`).

What we extract from the XML:

* **Catalog**: one row pinning the BSI 200-2 / Edition 2023 metadata.
* **Layers** (10): from the top-level ``<chapter>`` titles whose names match
  ``"<UPPERCODE> <title>"`` (ISMS Sicherheitsmanagement, ORP Organisation
  und Personal, …).
* **Modules** (~111): from ``<section>`` titles like ``"APP.1.1 Office-Produkte"``.
* **Module description** + **threat-situation intro**: the per-Baustein
  Beschreibung / Gefährdungslage prose.
* **Baustein-specific threat scenarios**: the named sub-sections inside the
  Gefährdungslage (e.g. CON.3's "Ransomware").
* **Responsible role per module**: read from the small "Grundsätzlich
  zuständig"-table that each Baustein renders.
* **Roles** (~29): union of every module's responsible role and every role
  named in square brackets behind a requirement title.
* **Requirements** (~2125): from ``<section>`` titles like
  ``"APP.1.1.A2 Einschränken von Aktiven Inhalten (B) [IT-Betrieb]"``. The
  ``(B|S|H)`` marker becomes ``level``; bracketed roles become the requirement's
  ``requirement_role`` M:N rows.
* **Requirement prose**: the body paragraphs of the section.
* **Threats** (47 elementary G 0.x).
* **Threat descriptions**.
* **R1/R2/R3 priority class**: from the "Schichtenmodell und Modellierung"
  chapter's table.

Idempotency: a re-import for the same catalog edition deletes the existing
catalog row first (cascading wipes everything dependent), then re-inserts.
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

DOCBOOK_NS = "http://docbook.org/ns/docbook"
NS = {"db": DOCBOOK_NS}

CATALOG_NAME = "BSI IT-Grundschutz 200-2"
DEFAULT_EDITION = "2023"
DEPRECATED_LABEL = "ENTFALLEN"

# Title regexes, applied in order. The Anforderung pattern must come first
# because it shares a prefix with the Baustein pattern.
RE_REQ = re.compile(
    # Accepts both "APP.1.1.A2" (canonical) and "OPS.2.3A22" (typo in the
    # 2023 XML). The typo form is rewritten via TYPO_FIXES below.
    r"^(?P<code>[A-Z]+(?:\.\d+)+\.?A\d+)"
    r"\s+(?P<title>.+?)"
    r"(?:\s+\((?P<marker>[BSH])\))?"
    r"(?:\s+\[(?P<roles>[^\]]*)\])?"
    r"\s*$"
)
RE_THREAT = re.compile(r"^(?P<code>G \d+\.\d+)\s+(?P<title>.+)$")
RE_MODULE = re.compile(r"^(?P<code>[A-Z]+(?:\.\d+)+)\s+(?P<title>.+)$")
RE_LAYER_CHAPTER = re.compile(r"^(?P<code>[A-Z]{2,5})\s+(?P<title>.+)$")
RE_BAUSTEIN_CODE = re.compile(r"^(?P<code>[A-Z]+(?:\.\d+)+)(?:\s|$)")

# Known typos in the BSI 2023 XML where a code differs from its canonical
# spelling (and from the KRT spelling).
TYPO_FIXES: dict[str, str] = {
    # The XML title for this requirement reads "OPS.2.3A22" (missing dot
    # before "A22"). The KRT spells it correctly as "OPS.2.3.A22".
    "OPS.2.3A22": "OPS.2.3.A22",
}

# Role-name normalisation: BSI uses short forms in requirement title
# brackets ("[OT-Betrieb]") but full forms in the per-module
# "Grundsätzlich zuständig"-table. We normalise to the full form so the
# role table doesn't end up with parallel rows for the same role.
ROLE_NAME_FIXES: dict[str, str] = {
    "OT-Betrieb": "OT-Betrieb (Operational Technology, OT)",
    "Informationssicherheitsbeauftragte": "Informationssicherheitsbeauftragte (ISB)",
}

LEVEL_BY_MARKER = {"B": "Basis", "S": "Standard", "H": "Hoch"}

# DocBook section titles we recognise inside a module body.
MODULE_DESCRIPTION_TITLES = {
    "Beschreibung",
    "Einleitung",
    "Zielsetzung",
    "Abgrenzung und Modellierung",
}
MODULE_THREAT_TITLE = "Gefährdungslage"

# Layer codes we accept (anything else with an UPPER-prefix title in the
# top-level chapters is ignored). Belt-and-suspenders against picking up
# "Glossar" or "Vorwort" style chapters that happen to start with caps.
KNOWN_LAYER_CODES = {
    "ISMS",
    "ORP",
    "CON",
    "OPS",
    "DER",
    "APP",
    "SYS",
    "IND",
    "NET",
    "INF",
}


@dataclass
class XmlImportStats:
    layers: int = 0
    roles: int = 0
    modules: int = 0
    requirements: int = 0
    deprecated_requirements: int = 0
    requirement_roles: int = 0
    threats: int = 0
    module_specific_threats: int = 0
    priority_classes_assigned: int = 0
    requirements_missing_role_bracket: int = 0
    requirements_missing_level: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def import_xml(
    conn: sqlite3.Connection,
    xml_path: Path,
    edition: str = DEFAULT_EDITION,
    source_url: str | None = None,
) -> XmlImportStats:
    """Build the full catalog from one BSI DocBook XML file.

    Replaces any existing catalog row for the same edition (CASCADE wipes
    everything dependent). Runs inside a single transaction.
    """
    tree = ET.parse(Path(xml_path))
    root = tree.getroot()
    stats = XmlImportStats()

    try:
        with conn:
            catalog_id = _replace_catalog(conn, edition, source_url)
            _import_layers(conn, catalog_id, root, stats)
            _import_threats(conn, catalog_id, root, stats)
            _import_modules_and_requirements(conn, catalog_id, root, stats)
            _assign_priority_classes(conn, catalog_id, root, stats)
    except Exception:
        log.exception("xml_import_failed")
        raise

    log.info(
        "xml_import_done",
        catalog=CATALOG_NAME,
        edition=edition,
        **stats.as_dict(),
    )
    return stats


# ---------------------------------------------------------------------------
# Catalog row
# ---------------------------------------------------------------------------


def _replace_catalog(
    conn: sqlite3.Connection,
    edition: str,
    source_url: str | None,
) -> int:
    conn.execute(
        "DELETE FROM catalog WHERE name = ? AND edition = ?",
        (CATALOG_NAME, edition),
    )
    cur = conn.execute(
        "INSERT INTO catalog (name, edition, source_url, imported_at) VALUES (?, ?, ?, ?)",
        (CATALOG_NAME, edition, source_url, _now_iso()),
    )
    catalog_id = cur.lastrowid
    assert catalog_id is not None
    return catalog_id


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


def _import_layers(
    conn: sqlite3.Connection,
    catalog_id: int,
    root: ET.Element,
    stats: XmlImportStats,
) -> None:
    """One row per top-level <chapter> whose title is "<CODE> <name>"."""
    for chapter in root.findall(f"{{{DOCBOOK_NS}}}chapter"):
        title = _section_title(chapter)
        if not title:
            continue
        m = RE_LAYER_CHAPTER.match(title)
        if not m or m.group("code") not in KNOWN_LAYER_CODES:
            continue
        conn.execute(
            "INSERT INTO layer (catalog_id, code, title) VALUES (?, ?, ?)",
            (catalog_id, m.group("code"), m.group("title").strip()),
        )
        stats.layers += 1


# ---------------------------------------------------------------------------
# Elementary threats (G 0.x)
# ---------------------------------------------------------------------------


def _import_threats(
    conn: sqlite3.Connection,
    catalog_id: int,
    root: ET.Element,
    stats: XmlImportStats,
) -> None:
    """Walk all sections, take the ones whose title starts with "G n.m"."""
    for section in root.iter(f"{{{DOCBOOK_NS}}}section"):
        title = _section_title(section)
        if not title:
            continue
        m = RE_THREAT.match(title)
        if not m:
            continue
        description = _collect_block_text(section, skip_titles=True)
        conn.execute(
            "INSERT INTO threat (catalog_id, code, title, description) VALUES (?, ?, ?, ?)",
            (catalog_id, m.group("code"), m.group("title").strip(), description),
        )
        stats.threats += 1


# ---------------------------------------------------------------------------
# Modules + requirements (the bulk of the work)
# ---------------------------------------------------------------------------


def _import_modules_and_requirements(
    conn: sqlite3.Connection,
    catalog_id: int,
    root: ET.Element,
    stats: XmlImportStats,
) -> None:
    """Walk every <section>, identify Bausteine, recurse for requirements.

    Done in a single pass with code-pattern dispatch. Layer lookups are
    cached so we don't fire a SELECT per Baustein.
    """
    layer_id_by_code = {
        r["code"]: r["id"]
        for r in conn.execute(
            "SELECT id, code FROM layer WHERE catalog_id = ?",
            (catalog_id,),
        )
    }
    role_cache: dict[str, int] = {}

    def role_id(name: str) -> int:
        key = ROLE_NAME_FIXES.get(name.strip(), name.strip())
        if key not in role_cache:
            cur = conn.execute(
                "INSERT OR IGNORE INTO role (catalog_id, name) VALUES (?, ?)",
                (catalog_id, key),
            )
            if cur.rowcount > 0:
                role_cache[key] = cur.lastrowid  # type: ignore[assignment]
                stats.roles += 1
            else:
                row = conn.execute(
                    "SELECT id FROM role WHERE catalog_id = ? AND name = ?",
                    (catalog_id, key),
                ).fetchone()
                role_cache[key] = row["id"]
        return role_cache[key]

    for section in root.iter(f"{{{DOCBOOK_NS}}}section"):
        title = _section_title(section)
        if not title:
            continue
        # Anforderung pattern is more specific - we handle it below as a
        # descendant of its Baustein. Threats already handled above.
        if RE_REQ.match(title):
            continue
        if RE_THREAT.match(title):
            continue
        m = RE_MODULE.match(title)
        if not m:
            continue
        bau_code = m.group("code")
        bau_title = m.group("title").strip()
        layer_code = bau_code.split(".", 1)[0]
        layer_id = layer_id_by_code.get(layer_code)
        if layer_id is None:
            log.warning(
                "module_layer_unknown",
                baustein=bau_code,
                layer=layer_code,
            )
            continue

        description = _extract_module_description(section)
        threat_situation = _extract_module_threat_situation(section)
        responsible_role_name = _extract_responsible_role(section)
        responsible_role_id = role_id(responsible_role_name) if responsible_role_name else None

        cur = conn.execute(
            "INSERT INTO module "
            "(catalog_id, layer_id, code, title, description, threat_situation, "
            " responsible_role_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                catalog_id,
                layer_id,
                bau_code,
                bau_title,
                description,
                threat_situation,
                responsible_role_id,
            ),
        )
        module_id = cur.lastrowid
        assert module_id is not None
        stats.modules += 1

        # Specific threat scenarios (Stage 2 functionality).
        for ordering, t_title, t_desc in _extract_module_specific_threats(section):
            conn.execute(
                "INSERT INTO module_specific_threat "
                "(module_id, ordering, title, description) "
                "VALUES (?, ?, ?, ?)",
                (module_id, ordering, t_title, t_desc),
            )
            stats.module_specific_threats += 1

        # Requirements (descendants only - not the module itself). Fall back
        # to the module's responsible role when an Anforderung's title has no
        # role bracket - that's the BSI convention per Standard 200-2.
        for req_section in _descendant_requirement_sections(section):
            _import_one_requirement(
                conn=conn,
                catalog_id=catalog_id,
                module_id=module_id,
                req_section=req_section,
                role_id=role_id,
                default_role_id=responsible_role_id,
                stats=stats,
            )


def _import_one_requirement(
    *,
    conn: sqlite3.Connection,
    catalog_id: int,
    module_id: int,
    req_section: ET.Element,
    role_id,
    default_role_id: int | None,
    stats: XmlImportStats,
) -> None:
    title_text = _section_title(req_section)
    if not title_text:
        return
    m = RE_REQ.match(title_text)
    if not m:
        return

    code = TYPO_FIXES.get(m.group("code"), m.group("code"))
    title = m.group("title").strip()
    marker = m.group("marker")
    role_block = m.group("roles") or ""

    if marker is None:
        # Without a level marker we cannot satisfy the schema CHECK. Skip
        # but log; in the 2023 catalog this should never happen.
        stats.requirements_missing_level += 1
        log.warning("requirement_no_level_marker", code=code, title=title_text)
        return
    level = LEVEL_BY_MARKER[marker]
    is_deprecated = 1 if title.strip().upper() == DEPRECATED_LABEL else 0
    prose = _collect_requirement_prose(req_section)

    cur = conn.execute(
        "INSERT INTO requirement "
        "(catalog_id, module_id, code, title, level, prose, is_deprecated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (catalog_id, module_id, code, title, level, prose, is_deprecated),
    )
    req_id = cur.lastrowid
    assert req_id is not None
    stats.requirements += 1
    if is_deprecated:
        stats.deprecated_requirements += 1

    # Bracketed roles, comma-separated. Some labels contain commas inside
    # parentheses ("OT-Betrieb (Operational Technology, OT)") so we split
    # only on commas outside any open paren.
    role_names = _split_roles(role_block)
    if role_names:
        role_ids = {role_id(n) for n in role_names}
    else:
        # BSI 200-2 convention: if no explicit role is named on a
        # requirement, the Baustein's "Grundsätzlich zuständig" role
        # applies.
        stats.requirements_missing_role_bracket += 1
        role_ids = {default_role_id} if default_role_id is not None else set()
    for rid in role_ids:
        cur = conn.execute(
            "INSERT OR IGNORE INTO requirement_role (requirement_id, role_id) VALUES (?, ?)",
            (req_id, rid),
        )
        if cur.rowcount > 0:
            stats.requirement_roles += 1


def _split_roles(text: str) -> list[str]:
    """Split a role-bracket value on top-level commas only."""
    if not text:
        return []
    out, depth, buf = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
            buf += ch
        elif ch == ")":
            depth = max(0, depth - 1)
            buf += ch
        elif ch == "," and depth == 0:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def _descendant_requirement_sections(
    module_section: ET.Element,
) -> Iterator[ET.Element]:
    """Yield <section> elements anywhere inside module_section whose title
    matches the requirement pattern.

    Walks deeply so we don't care if upstream changes how the requirement
    sections are nested inside Basis-/Standard-/Hoch-buckets.
    """
    for sec in module_section.iter(f"{{{DOCBOOK_NS}}}section"):
        if sec is module_section:
            continue
        title = _section_title(sec)
        if title and RE_REQ.match(title):
            yield sec


# ---------------------------------------------------------------------------
# Per-module prose extraction (description + threat-situation intro + specific)
# ---------------------------------------------------------------------------


def _extract_module_description(module_section: ET.Element) -> str | None:
    """Concatenate text from the module's "Beschreibung" sub-section."""
    desc_section = _find_child_section(module_section, "Beschreibung")
    if desc_section is None:
        return None
    parts: list[str] = []
    intro = _direct_block_text(desc_section)
    if intro:
        parts.append(intro)
    for child in _child_sections(desc_section):
        sub_title = _section_title(child)
        if sub_title in MODULE_DESCRIPTION_TITLES:
            text = _collect_block_text(child, skip_titles=True)
            if text:
                parts.append(text)
    return "\n\n".join(parts) if parts else None


def _extract_module_threat_situation(module_section: ET.Element) -> str | None:
    """Return the intro paragraphs of the module's "Gefährdungslage" section.

    The named sub-section threat scenarios that follow are captured separately
    by :func:`_extract_module_specific_threats` and live in their own table.
    """
    gl = _find_child_section(module_section, MODULE_THREAT_TITLE)
    if gl is None:
        return None
    text = _direct_block_text(gl)
    return text or None


def _extract_module_specific_threats(
    module_section: ET.Element,
) -> list[tuple[int, str, str | None]]:
    """Return ``(ordering, title, description)`` for each named threat scenario."""
    gl = _find_child_section(module_section, MODULE_THREAT_TITLE)
    if gl is None:
        return []
    out: list[tuple[int, str, str | None]] = []
    for index, child in enumerate(_child_sections(gl), start=1):
        title = _section_title(child)
        if not title:
            continue
        description = _direct_block_text(child) or None
        out.append((index, title, description))
    return out


def _extract_responsible_role(module_section: ET.Element) -> str | None:
    """Pull the "Grundsätzlich zuständig"-role from the module's intro table.

    BSI renders a small 2-row table that says:
        | Grundsätzlich zuständig | <Rollenname>            |
        | Weitere Zuständigkeiten | <ggf. weitere Rollen>   |
    We walk the table rows directly and read the right-hand <td>.
    """
    for tr in module_section.iter(f"{{{DOCBOOK_NS}}}tr"):
        cells = list(tr.findall(f"{{{DOCBOOK_NS}}}td"))
        if len(cells) < 2:
            continue
        left = _normalize_whitespace(_text_of(cells[0]))
        if left.startswith("Grundsätzlich zuständig"):
            right = _normalize_whitespace(_text_of(cells[1]))
            return right or None
    return None


def _collect_requirement_prose(req_section: ET.Element) -> str | None:
    """Full prose of one requirement (every <para>/<itemizedlist>)."""
    text = _collect_block_text(req_section, skip_titles=True)
    return text or None


# ---------------------------------------------------------------------------
# Priority classes (R1 / R2 / R3) from "Schichtenmodell und Modellierung"
# ---------------------------------------------------------------------------


def _assign_priority_classes(
    conn: sqlite3.Connection,
    catalog_id: int,
    root: ET.Element,
    stats: XmlImportStats,
) -> None:
    """Set ``module.priority_class`` from the modelling chapter's R1/R2/R3 table."""
    mapping = _extract_priority_mapping(root)
    if not mapping:
        log.warning("priority_table_not_found")
        return
    for code, prio in mapping.items():
        cur = conn.execute(
            "UPDATE module SET priority_class = ? WHERE catalog_id = ? AND code = ?",
            (prio, catalog_id, code),
        )
        if cur.rowcount > 0:
            stats.priority_classes_assigned += 1


def _extract_priority_mapping(root: ET.Element) -> dict[str, str]:
    """Walk every <tr> in the catalog and return Baustein -> R1/R2/R3."""
    mapping: dict[str, str] = {}
    for tr in root.iter(f"{{{DOCBOOK_NS}}}tr"):
        cells = [_normalize_whitespace(_text_of(td)) for td in tr.findall(f"{{{DOCBOOK_NS}}}td")]
        if len(cells) < 2:
            continue
        prio = None
        code = None
        for c in cells:
            if c in ("R1", "R2", "R3"):
                prio = c
            elif (m := RE_BAUSTEIN_CODE.match(c)) is not None:
                code = m.group("code")
        if prio and code:
            mapping[code] = prio
    return mapping


# ---------------------------------------------------------------------------
# Low-level XML helpers
# ---------------------------------------------------------------------------


def _section_title(section: ET.Element) -> str | None:
    title_el = section.find(f"{{{DOCBOOK_NS}}}title")
    if title_el is None:
        return None
    return _normalize_whitespace(_text_of(title_el))


def _find_child_section(parent: ET.Element, title: str) -> ET.Element | None:
    for child in _child_sections(parent):
        if _section_title(child) == title:
            return child
    return None


def _child_sections(parent: ET.Element) -> Iterator[ET.Element]:
    """Yield direct <section> children (not nested deeper)."""
    for el in parent:
        if el.tag == f"{{{DOCBOOK_NS}}}section":
            yield el


def _direct_block_text(section: ET.Element) -> str:
    """Concatenate <para>/<itemizedlist> children directly under ``section``.

    Stops at the first nested <section>. The section's own <title> is
    dropped.
    """
    parts: list[str] = []
    for el in section:
        if el.tag == f"{{{DOCBOOK_NS}}}section":
            break
        if el.tag == f"{{{DOCBOOK_NS}}}title":
            continue
        rendered = _render_block(el)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)


def _collect_block_text(section: ET.Element, *, skip_titles: bool) -> str:
    """Render every direct para/list/etc. child of ``section`` to plain text."""
    parts: list[str] = []
    for el in section:
        if el.tag == f"{{{DOCBOOK_NS}}}section":
            continue
        if skip_titles and el.tag == f"{{{DOCBOOK_NS}}}title":
            continue
        if el.tag == f"{{{DOCBOOK_NS}}}title":
            continue
        rendered = _render_block(el)
        if rendered:
            parts.append(rendered)
    return "\n\n".join(parts)


def _render_block(el: ET.Element) -> str:
    """Render one para/list element to plain text."""
    tag = _localname(el.tag)
    if tag == "para":
        return _normalize_whitespace(_text_of(el))
    if tag in ("itemizedlist", "orderedlist"):
        bullets = []
        for li in el.findall(f"{{{DOCBOOK_NS}}}listitem"):
            line = _normalize_whitespace(_text_of(li))
            if line:
                bullets.append(f"- {line}")
        return "\n".join(bullets)
    return _normalize_whitespace(_text_of(el))


def _text_of(el: ET.Element) -> str:
    return "".join(el.itertext())


def _normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
