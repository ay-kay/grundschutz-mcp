# Changelog

All notable changes are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning
follows [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Initial open-source release.
- Full ingest pipeline: schema → JSON → DocBook XML → cross-references → embeddings.
- 16 MCP tools (layers, modules, requirements, threats, cross-refs, hybrid/keyword/semantic search).
- Hybrid search via Reciprocal Rank Fusion over FTS5 + sqlite-vec.
- `BAAI/bge-m3` default embedding model with 8 k context (covers full Baustein text).
- R1/R2/R3 implementation priority from the BSI modelling chapter.
- Baustein-specific threat scenarios (701 items extracted from Gefährdungslage sub-sections).
- Docker production deployment with Traefik integration, rate limiting, CPU/memory caps.
- 84 automated tests including end-to-end MCP transport test.
- No BSI-derived content is shipped with the repository. The build pipeline
  fetches the DocBook XML via `make fetch-xml` and the Kreuzreferenztabelle
  XLSX via `make fetch-krt` directly from bsi.bund.de at setup time.
  Parsers are clean-room implementations against the BSI source format;
  no third-party Kompendium-parsing code is bundled. See
  [NOTICE.md](NOTICE.md) for licensing.
