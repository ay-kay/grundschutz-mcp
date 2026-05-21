"""Shared pytest fixtures."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from grundschutz_mcp.db import apply_schema, connect


def _seed_tiny_catalog(conn: sqlite3.Connection) -> None:
    """Insert a small but structurally complete catalog directly via SQL.

    Two layers (APP, ORP), two roles, two modules, three requirements
    (one deprecated), two threats, two requirement<->threat links with
    protection goals on one of them. Just enough to drive most tool /
    search / server tests without involving the real importers.
    """
    conn.executescript("""
        INSERT INTO catalog (name, edition, imported_at) VALUES
            ('BSI IT-Grundschutz 200-2', '2023', '2026-01-01T00:00:00Z');

        INSERT INTO layer (catalog_id, code, title) VALUES
            (1, 'APP', 'Anwendungen'),
            (1, 'ORP', 'Organisation und Personal');

        INSERT INTO role (catalog_id, name) VALUES
            (1, 'IT-Betrieb'),
            (1, 'Benutzende');

        INSERT INTO module
            (catalog_id, layer_id, code, title, description,
             threat_situation, responsible_role_id, priority_class)
        VALUES
            (1, 1, 'APP.1.1', 'Office-Produkte',
             'Office-Produkte schützen.',
             'Verschiedene Bedrohungen für Office.', 1, 'R2'),
            (1, 2, 'ORP.1', 'Organisation', NULL, NULL, 1, 'R1');

        INSERT INTO requirement
            (catalog_id, module_id, code, title, level, prose, is_deprecated)
        VALUES
            (1, 1, 'APP.1.1.A1', 'ENTFALLEN', 'Basis', NULL, 1),
            (1, 1, 'APP.1.1.A2', 'Einschränken von Aktiven Inhalten',
             'Basis',
             'Aktive Inhalte einschränken. Siehe auch APP.1.1.A1 und ORP.1.',
             0),
            (1, 2, 'ORP.1.A1', 'Sicherheitsleitlinie', 'Standard', NULL, 0);

        INSERT INTO requirement_role (requirement_id, role_id) VALUES
            (1, 1),         -- APP.1.1.A1 -> IT-Betrieb
            (2, 1), (2, 2), -- APP.1.1.A2 -> IT-Betrieb, Benutzende
            (3, 1);         -- ORP.1.A1 -> IT-Betrieb

        INSERT INTO threat (catalog_id, code, title, description) VALUES
            (1, 'G 0.14', 'Ausspähen von Informationen (Spionage)',
             'Spionage-Beschreibung.'),
            (1, 'G 0.39', 'Schadprogramme', NULL);

        INSERT INTO requirement_threat (requirement_id, threat_id) VALUES
            (2, 2),   -- APP.1.1.A2 addresses G 0.39
            (3, 1);   -- ORP.1.A1 addresses G 0.14

        INSERT INTO requirement_threat_goal
            (requirement_threat_id, goal) VALUES
            (2, 'C'), (2, 'I');   -- ORP.1.A1 -> G 0.14 with C+I

        INSERT INTO module_specific_threat
            (module_id, ordering, title, description) VALUES
            (1, 1, 'Schadsoftware in Office-Dokumenten',
             'Makros können Schadcode enthalten.'),
            (1, 2, 'Datenverlust durch Fehlbedienung',
             'Versehentliches Löschen wichtiger Dokumente.');
    """)


@pytest.fixture
def tiny_db(tmp_path: Path) -> sqlite3.Connection:
    """Open a connection to a fresh DB and seed the tiny catalog."""
    conn = connect(tmp_path / "tiny.db")
    apply_schema(conn)
    _seed_tiny_catalog(conn)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def tiny_db_with_vec(tmp_path: Path) -> sqlite3.Connection:
    """Like :func:`tiny_db` but loads the sqlite-vec extension."""
    conn = connect(tmp_path / "tiny_vec.db", load_vec=True)
    apply_schema(conn)
    _seed_tiny_catalog(conn)
    conn.commit()
    yield conn
    conn.close()
