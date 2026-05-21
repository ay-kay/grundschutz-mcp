-- ============================================================================
-- grundschutz-mcp: Database Schema
-- ============================================================================
-- BSI IT-Grundschutz-Kompendium (Edition 2023, BSI-Standard 200-2)
-- Designed for SQLite 3.38+ with FTS5 and sqlite-vec extension.
--
-- Conventions:
--   * Surrogate INTEGER PRIMARY KEYs for joins.
--   * Business codes (e.g. "APP.1.1.A2") kept as TEXT with UNIQUE constraints.
--   * Catalog-scoped uniqueness to allow future Grundschutz++ side-by-side.
--   * Foreign keys ON for referential integrity (enable per connection).
-- ============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- ============================================================================
-- Catalogs
-- ----------------------------------------------------------------------------
-- A catalog is one source body (e.g. "BSI IT-Grundschutz 200-2", edition
-- "2023"). Later: a second row for "BSI Grundschutz++ 1.0" can be added
-- without touching tools or the rest of the schema.
-- ============================================================================
CREATE TABLE catalog (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,           -- "BSI IT-Grundschutz 200-2"
    edition      TEXT NOT NULL,           -- "2023"
    source_url   TEXT,                    -- official BSI URL, optional
    imported_at  TEXT NOT NULL,           -- ISO 8601 timestamp
    UNIQUE (name, edition)
);

-- ============================================================================
-- Layers (Bausteinkategorien)
-- ----------------------------------------------------------------------------
-- The 10 top-level layers: APP, CON, DER, IND, INF, ISMS, NET, OPS, ORP, SYS.
-- ============================================================================
CREATE TABLE layer (
    id         INTEGER PRIMARY KEY,
    catalog_id INTEGER NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
    code       TEXT NOT NULL,             -- "APP"
    title      TEXT NOT NULL,             -- "Anwendungen"
    UNIQUE (catalog_id, code)
);

-- ============================================================================
-- Roles (Rollen)
-- ----------------------------------------------------------------------------
-- Roles such as "IT-Betrieb", "ISB", "Benutzende". M:N with requirements.
-- Also referenced as the module's primary responsible role.
-- ============================================================================
CREATE TABLE role (
    id         INTEGER PRIMARY KEY,
    catalog_id INTEGER NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,             -- "IT-Betrieb"
    UNIQUE (catalog_id, name)
);

-- ============================================================================
-- Modules (Bausteine)
-- ----------------------------------------------------------------------------
-- A module is a building block such as "APP.1.1 Office-Produkte".
-- Text fields (description, threat_situation) are filled by the XML importer.
-- ============================================================================
CREATE TABLE module (
    id                  INTEGER PRIMARY KEY,
    catalog_id          INTEGER NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
    layer_id            INTEGER NOT NULL REFERENCES layer(id),
    code                TEXT NOT NULL,    -- "APP.1.1"
    title               TEXT NOT NULL,    -- "Office-Produkte"
    description         TEXT,             -- full prose of "Beschreibung" / intro
    threat_situation    TEXT,             -- full prose of "Gefährdungslage" intro
    responsible_role_id INTEGER REFERENCES role(id),
    -- Implementation priority from chapter "Schichtenmodell und Modellierung"
    -- of the Kompendium: R1 = vorrangig (e.g. ISMS.1, all ORP),
    -- R2 = anschließend, R3 = zuletzt. Filled by the XML importer.
    priority_class      TEXT CHECK (priority_class IN ('R1','R2','R3')),
    UNIQUE (catalog_id, code)
);

CREATE INDEX idx_module_layer ON module(layer_id);

-- ============================================================================
-- Requirements (Anforderungen)
-- ----------------------------------------------------------------------------
-- Single security requirement such as "APP.1.1.A2".
--
-- level     : "Basis" | "Standard" | "Hoch" (from BSI 200-2)
-- modal_verb: "MUSS" | "SOLLTE" | "KANN"     (reserved for Grundschutz++)
-- prose     : populated by the XML importer
-- is_deprecated = 1  if the XML title is "ENTFALLEN"
-- ============================================================================
CREATE TABLE requirement (
    id            INTEGER PRIMARY KEY,
    catalog_id    INTEGER NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
    module_id     INTEGER NOT NULL REFERENCES module(id),
    code          TEXT NOT NULL,          -- "APP.1.1.A2"
    title         TEXT NOT NULL,          -- "Einschränken von Aktiven Inhalten"
    level         TEXT NOT NULL CHECK (level IN ('Basis','Standard','Hoch')),
    prose         TEXT,                   -- full requirement text
    is_deprecated INTEGER NOT NULL DEFAULT 0 CHECK (is_deprecated IN (0,1)),
    modal_verb    TEXT,                   -- nullable in 200-2, used by GS++
    UNIQUE (catalog_id, code)
);

CREATE INDEX idx_requirement_module ON requirement(module_id);
CREATE INDEX idx_requirement_level  ON requirement(level);

-- ----------------------------------------------------------------------------
-- M:N Requirement <-> Role
-- ----------------------------------------------------------------------------
CREATE TABLE requirement_role (
    requirement_id INTEGER NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
    role_id        INTEGER NOT NULL REFERENCES role(id)        ON DELETE CASCADE,
    PRIMARY KEY (requirement_id, role_id)
);

CREATE INDEX idx_reqrole_role ON requirement_role(role_id);

-- ============================================================================
-- Elementary Threats (Elementare Gefährdungen, G 0.x)
-- ----------------------------------------------------------------------------
-- The 47 elementary threats. Description is filled by the XML importer.
-- ============================================================================
CREATE TABLE threat (
    id          INTEGER PRIMARY KEY,
    catalog_id  INTEGER NOT NULL REFERENCES catalog(id) ON DELETE CASCADE,
    code        TEXT NOT NULL,            -- "G 0.14"
    title       TEXT NOT NULL,            -- "Ausspähen von Informationen (Spionage)"
    description TEXT,                     -- full prose
    UNIQUE (catalog_id, code)
);

-- ----------------------------------------------------------------------------
-- M:N Requirement <-> Threat (BSI Kreuzreferenztabelle)
-- ----------------------------------------------------------------------------
-- One row = one requirement-addresses-threat link. Protection goals (C/I/A)
-- attached to this link via requirement_threat_goal.
-- ============================================================================
CREATE TABLE requirement_threat (
    id             INTEGER PRIMARY KEY,
    requirement_id INTEGER NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
    threat_id      INTEGER NOT NULL REFERENCES threat(id)      ON DELETE CASCADE,
    UNIQUE (requirement_id, threat_id)
);

CREATE INDEX idx_reqthreat_threat ON requirement_threat(threat_id);

-- ----------------------------------------------------------------------------
-- Protection goals (C/I/A) per requirement-threat link
-- ----------------------------------------------------------------------------
CREATE TABLE requirement_threat_goal (
    requirement_threat_id INTEGER NOT NULL
        REFERENCES requirement_threat(id) ON DELETE CASCADE,
    goal                  TEXT NOT NULL CHECK (goal IN ('C','I','A')),
    PRIMARY KEY (requirement_threat_id, goal)
);

-- ============================================================================
-- Module-specific threat scenarios (Gefährdungslage-Unterabschnitte)
-- ----------------------------------------------------------------------------
-- Each Baustein's "Gefährdungslage" section contains - besides the intro
-- paragraphs stored in module.threat_situation - a numbered list of named
-- threat scenarios that are specific to that Baustein. Example for CON.3
-- (Datensicherungskonzept):
--   1. Fehlende Datensicherung
--   2. Fehlende Wiederherstellungstests
--   3. Ungeeignete Aufbewahrung der Speichermedien
--   ... up to ~10 scenarios per Baustein
--
-- These are NOT the catalogue's 47 elementary threats (G 0.x). They are
-- baustein-specific narratives that sometimes correspond to one or more
-- elementary threats but carry additional context (e.g. "Ransomware" as a
-- scenario maps to G 0.39 but reads more concretely).
-- ============================================================================
CREATE TABLE module_specific_threat (
    id          INTEGER PRIMARY KEY,
    module_id   INTEGER NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    ordering    INTEGER NOT NULL,         -- position within Gefährdungslage
    title       TEXT NOT NULL,
    description TEXT,
    UNIQUE (module_id, ordering)
);

CREATE INDEX idx_mst_module ON module_specific_threat(module_id);

CREATE VIRTUAL TABLE module_specific_threat_fts USING fts5(
    title, description,
    content='module_specific_threat', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER module_specific_threat_ai AFTER INSERT ON module_specific_threat BEGIN
    INSERT INTO module_specific_threat_fts (rowid, title, description)
        VALUES (new.id, new.title, new.description);
END;
CREATE TRIGGER module_specific_threat_ad AFTER DELETE ON module_specific_threat BEGIN
    INSERT INTO module_specific_threat_fts (module_specific_threat_fts, rowid, title, description)
        VALUES('delete', old.id, old.title, old.description);
END;
CREATE TRIGGER module_specific_threat_au AFTER UPDATE ON module_specific_threat BEGIN
    INSERT INTO module_specific_threat_fts (module_specific_threat_fts, rowid, title, description)
        VALUES('delete', old.id, old.title, old.description);
    INSERT INTO module_specific_threat_fts (rowid, title, description)
        VALUES (new.id, new.title, new.description);
END;

-- ============================================================================
-- Cross-references between requirements
-- ----------------------------------------------------------------------------
-- Extracted from requirement prose with a regex on patterns like "APP.1.1.A2".
-- Target stored as TEXT (not FK) because:
--   * Some cited codes refer to deprecated/renamed requirements.
--   * Some cite modules, not requirements (handled by target_kind).
-- ============================================================================
CREATE TABLE cross_reference (
    id               INTEGER PRIMARY KEY,
    source_req_id    INTEGER NOT NULL REFERENCES requirement(id) ON DELETE CASCADE,
    target_code      TEXT NOT NULL,        -- "APP.1.1.A2" or "APP.1.1"
    target_kind      TEXT NOT NULL CHECK (target_kind IN ('requirement','module')),
    UNIQUE (source_req_id, target_code)
);

CREATE INDEX idx_crossref_target ON cross_reference(target_code);

-- ============================================================================
-- Import issues (validation log)
-- ----------------------------------------------------------------------------
-- The importers write here whenever something is suspicious, e.g. JSON level
-- doesn't match XML "(B|S|H)" marker, or a requirement is in JSON but has no
-- prose in the XML. Inspect with: SELECT * FROM import_issue ORDER BY severity;
-- ============================================================================
CREATE TABLE import_issue (
    id          INTEGER PRIMARY KEY,
    catalog_id  INTEGER REFERENCES catalog(id) ON DELETE CASCADE,
    severity    TEXT NOT NULL CHECK (severity IN ('info','warning','error')),
    entity_type TEXT,                      -- "module" | "requirement" | "threat" | ...
    entity_code TEXT,                      -- e.g. "APP.1.1.A2"
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_issue_severity ON import_issue(severity);
CREATE INDEX idx_issue_entity   ON import_issue(entity_type, entity_code);

-- ============================================================================
-- Embedding metadata
-- ----------------------------------------------------------------------------
-- Records which embedding model produced the vectors currently in the vec
-- tables. Allows safe re-embedding when the model changes.
-- ============================================================================
CREATE TABLE embedding_meta (
    id          INTEGER PRIMARY KEY,
    model_name  TEXT NOT NULL,            -- "intfloat/multilingual-e5-large"
    dimensions  INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

-- ============================================================================
-- FTS5 virtual tables (keyword search)
-- ----------------------------------------------------------------------------
-- These are "contentless-external" tables: they reference the source table
-- via content=/content_rowid= and stay in sync via triggers below.
-- Tokenizer: unicode61 with diacritic folding, so "Protokollierung" matches
-- "protokollierung" and "Verschlusselung" matches "Verschlüsselung".
-- ============================================================================
CREATE VIRTUAL TABLE requirement_fts USING fts5(
    code, title, prose,
    content='requirement', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE module_fts USING fts5(
    code, title, description, threat_situation,
    content='module', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE threat_fts USING fts5(
    code, title, description,
    content='threat', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- ----------------------------------------------------------------------------
-- Sync triggers: keep FTS tables aligned with their source tables.
-- ----------------------------------------------------------------------------
CREATE TRIGGER requirement_ai AFTER INSERT ON requirement BEGIN
    INSERT INTO requirement_fts (rowid, code, title, prose)
        VALUES (new.id, new.code, new.title, new.prose);
END;
CREATE TRIGGER requirement_ad AFTER DELETE ON requirement BEGIN
    INSERT INTO requirement_fts (requirement_fts, rowid, code, title, prose)
        VALUES('delete', old.id, old.code, old.title, old.prose);
END;
CREATE TRIGGER requirement_au AFTER UPDATE ON requirement BEGIN
    INSERT INTO requirement_fts (requirement_fts, rowid, code, title, prose)
        VALUES('delete', old.id, old.code, old.title, old.prose);
    INSERT INTO requirement_fts (rowid, code, title, prose)
        VALUES (new.id, new.code, new.title, new.prose);
END;

CREATE TRIGGER module_ai AFTER INSERT ON module BEGIN
    INSERT INTO module_fts (rowid, code, title, description, threat_situation)
        VALUES (new.id, new.code, new.title, new.description, new.threat_situation);
END;
CREATE TRIGGER module_ad AFTER DELETE ON module BEGIN
    INSERT INTO module_fts (module_fts, rowid, code, title, description, threat_situation)
        VALUES('delete', old.id, old.code, old.title, old.description, old.threat_situation);
END;
CREATE TRIGGER module_au AFTER UPDATE ON module BEGIN
    INSERT INTO module_fts (module_fts, rowid, code, title, description, threat_situation)
        VALUES('delete', old.id, old.code, old.title, old.description, old.threat_situation);
    INSERT INTO module_fts (rowid, code, title, description, threat_situation)
        VALUES (new.id, new.code, new.title, new.description, new.threat_situation);
END;

CREATE TRIGGER threat_ai AFTER INSERT ON threat BEGIN
    INSERT INTO threat_fts (rowid, code, title, description)
        VALUES (new.id, new.code, new.title, new.description);
END;
CREATE TRIGGER threat_ad AFTER DELETE ON threat BEGIN
    INSERT INTO threat_fts (threat_fts, rowid, code, title, description)
        VALUES('delete', old.id, old.code, old.title, old.description);
END;
CREATE TRIGGER threat_au AFTER UPDATE ON threat BEGIN
    INSERT INTO threat_fts (threat_fts, rowid, code, title, description)
        VALUES('delete', old.id, old.code, old.title, old.description);
    INSERT INTO threat_fts (rowid, code, title, description)
        VALUES (new.id, new.code, new.title, new.description);
END;

-- ============================================================================
-- Vector tables (semantic search)
-- ----------------------------------------------------------------------------
-- Created here as comments because they require the sqlite-vec extension to
-- be loaded. The embedding importer should:
--   1. Load extension: SELECT load_extension('vec0');
--   2. Run the CREATE VIRTUAL TABLE statements below.
--   3. INSERT INTO ... VALUES (entity_id, embedding) with the model output.
--
-- Dimensions must match the configured embedding model. Default below is
-- 1024 for intfloat/multilingual-e5-large.
-- ============================================================================
--
-- CREATE VIRTUAL TABLE requirement_vec USING vec0(
--     id INTEGER PRIMARY KEY,
--     embedding FLOAT[1024]
-- );
--
-- CREATE VIRTUAL TABLE module_vec USING vec0(
--     id INTEGER PRIMARY KEY,
--     embedding FLOAT[1024]
-- );
--
-- CREATE VIRTUAL TABLE threat_vec USING vec0(
--     id INTEGER PRIMARY KEY,
--     embedding FLOAT[1024]
-- );

-- ============================================================================
-- Convenience views (read-only helpers for tool implementations)
-- ============================================================================
CREATE VIEW v_requirement_full AS
SELECT
    r.id,
    r.code,
    r.title,
    r.level,
    r.prose,
    r.is_deprecated,
    r.modal_verb,
    m.code  AS module_code,
    m.title AS module_title,
    l.code  AS layer_code,
    l.title AS layer_title
FROM requirement r
JOIN module m ON m.id = r.module_id
JOIN layer  l ON l.id = m.layer_id;

CREATE VIEW v_requirement_threats AS
SELECT
    rt.id              AS link_id,
    r.code             AS requirement_code,
    t.code             AS threat_code,
    t.title            AS threat_title,
    GROUP_CONCAT(g.goal, ',') AS protection_goals
FROM requirement_threat rt
JOIN requirement r ON r.id = rt.requirement_id
JOIN threat      t ON t.id = rt.threat_id
LEFT JOIN requirement_threat_goal g ON g.requirement_threat_id = rt.id
GROUP BY rt.id;