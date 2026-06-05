# Changelog

All notable changes are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [SemVer](https://semver.org/).

## [0.2.0] - 2026-06-05

### Changed
- `get_requirement` now also returns the requirement's C/I/A protection
  goals (`protection_goals`) from the BSI Kreuzreferenztabelle Grundwerte.
  An empty list means BSI assigned none, not that data is missing.
- `get_threats_for_requirement` reports `protection_goals` once at the top
  level — the goals belong to the requirement as a whole, not to individual
  threats. Threat entries are now just `code` + `title`.
- `list_modules` returns the whole 111-module catalogue by default
  (default limit 50 → 200), wrapped in an envelope
  `{modules, total_count, returned_count, truncated}`.

### Fixed
- `list_modules` no longer truncates silently. A capped result is flagged
  via `truncated` / `total_count`, so a model can no longer mistake the
  first 50 of 111 modules for the full list — which produced confidently
  wrong answers such as "only 4 R1 modules" instead of the correct 15.

## [0.1.0] - 2026-06-04

### Added
- Initial open-source release.
- Full ingest pipeline: schema → DocBook XML → KRT → cross-references → embeddings.
- 14 MCP tools (layers, modules, requirements, threats, cross-refs, hybrid/keyword/semantic search).
- Hybrid search via Reciprocal Rank Fusion over FTS5 + sqlite-vec.
- `BAAI/bge-m3` default embedding model with 8 k context (covers full Baustein text).
- R1/R2/R3 implementation priority from the BSI modelling chapter.
- Baustein-specific threat scenarios (701 items extracted from Gefährdungslage sub-sections).
- Docker production deployment with Traefik integration, rate limiting, CPU/memory caps.
- 80 automated tests including end-to-end MCP transport test.
- No BSI-derived content is shipped with the repository. The build pipeline
  fetches the DocBook XML via `make fetch-xml` and the Kreuzreferenztabelle
  XLSX via `make fetch-krt` directly from bsi.bund.de at setup time.
  Parsers are clean-room implementations against the BSI source format;
  no third-party Kompendium-parsing code is bundled. See
  [NOTICE.md](NOTICE.md) for licensing.
