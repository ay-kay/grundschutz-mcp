# grundschutz-mcp

An MCP server that gives AI agents structured, semantic access to the
**BSI IT-Grundschutz-Kompendium** (Edition 2023, BSI-Standard 200-2).
Built for ISMS teams who want their agents to look up Bausteine,
Anforderungen, elementare Gefährdungen, the BSI's R1/R2/R3 implementation
order, and the per-Baustein threat scenarios — instead of grepping a
800-page PDF.

> **Status**: stable. Imports the full 2023 corpus with 100 % prose
> coverage, exposes 16 MCP tools over HTTP, hybrid keyword + semantic
> search powered by `BAAI/bge-m3` (German-strong, 8k context, runs
> locally — no cloud APIs).

---

## What's in it

| Entity | Count | Source |
|---|---:|---|
| Schichten (layers) | 10 | BSI DocBook XML (chapter titles) |
| Bausteine | 111 | BSI DocBook XML |
| Anforderungen | 2125 | XML, with full prose, level (Basis/Standard/Hoch), roles |
| Elementare Gefährdungen (G 0.x) | 47 | XML, with description |
| Baustein-spezifische Gefährdungs­szenarien | 701 | XML Gefährdungslage sub-sections |
| Anforderung↔Gefährdung-Links | 5888 | BSI Kreuzreferenztabelle (XLSX) |
| Schutzziele C/I/A per link | 2820 | KRT |
| Cross-References between requirements | 118 | regex-extracted from prose |
| Embeddings | 2283 vectors | `BAAI/bge-m3`, 1024-dim |

---

## The 16 MCP tools

| Tool | What it returns |
|---|---|
| `list_layers()` | All 10 BSI layers (APP, CON, DER, IND, INF, ISMS, NET, OPS, ORP, SYS). |
| `list_modules(layer?, search?, priority?, limit?)` | Bausteine, filterable by layer, free-text and R1/R2/R3 priority. |
| `get_module(code)` | One Baustein incl. description, threat situation, **specific threats**, the requirements grouped by level. |
| `list_module_threats(code)` | Just the baustein-specific threat scenarios (e.g. "Ransomware", "Fehlende Wiederherstellungstests" for CON.3). |
| `list_requirements(module_code, level?, include_deprecated?)` | Requirements of one Baustein. |
| `get_requirement(code)` | One requirement with full prose, roles, module context. |
| `get_cross_references(req_code)` | Codes cited from a requirement's prose. |
| `list_threats()` | All 47 elementare Gefährdungen. |
| `get_threat(code)` | One Gefährdung with full description. |
| `get_threats_for_requirement(req_code)` | Threats addressed by a requirement, with per-link protection goals (C/I/A). |
| `get_requirements_for_threat(threat_code)` | Requirements that address a given threat. |
| `search(query, entity_types?, limit?)` | **Default search.** Hybrid keyword + semantic via Reciprocal Rank Fusion. |
| `search_keyword(query, …)` | Pure FTS5; use when you want exact-term matching without semantic generalisation. |
| `search_semantic(query, …)` | Pure dense vector search; rarely needed directly — `search` covers both. |

Unknown codes return a structured error with fuzzy-match suggestions
instead of throwing.

---

## Architecture

```
[BSI sources, downloaded directly]
   │
   ├── DocBook XML  (Kompendium 2023): structure + prose
   └── KRT XLSX     (Kreuzreferenztabelle): requirement <-> threat
                                            mapping with C/I/A goals
        │
        ▼
   [ingest pipeline:  xml -> krt -> crossref -> embeddings]
        │
        ▼
   ┌─────────────┐
   │  SQLite     │ ← FTS5 (auto via triggers)
   │  + WAL      │ ← sqlite-vec (1024-dim embeddings)
   └─────────────┘
        ▲
        │
   [Cross-reference extractor, embedding generator]
        │
        ▼
   [HTTP MCP server (FastMCP + Starlette + Traefik)]
        │
        ▼
   AI agents (Claude Desktop, Cowork, claude.ai, Cursor, …)
```

The three-layer split (ingest → DB → tools) means a future Grundschutz++
(OSCAL) importer only needs a new ingest module; the schema and the
tool layer stay.

---

## Quickstart

```bash
# 1. Clone (no BSI data is shipped with this repo)
git clone https://github.com/ay-kay/grundschutz-mcp.git
cd grundschutz-mcp

# 2. Configure
cp .env.example .env
$EDITOR .env       # most defaults are fine; tweak as needed

# 3. Fetch BSI source data directly from bsi.bund.de. Both files
#    are gitignored.
make fetch-xml     # DocBook XML, ~3 MB
make fetch-krt     # Kreuzreferenztabelle XLSX, ~350 KB
```

Then pick **one** of the deployment modes below.

### A) Standalone Docker (default)

Runs the server on `127.0.0.1:8080` of the host. Useful for local
development, for desktop-side integration (Claude Desktop via the
mcp-remote bridge), or behind your own existing reverse proxy
(any nginx/Caddy/HAProxy/Apache that can forward to `127.0.0.1:8080`).

```bash
make docker-up                     # docker compose up -d --build
curl http://localhost:8080/health
# -> {"status":"ok","requirement_count":2125}
```

First start downloads `bge-m3` (~2.3 GB) and embeds the catalog
(~15 min on Apple Silicon MPS, 60-120 min on x86 CPU only).
Restarts skip both - the model lives in `./hf-cache/`, the DB in
`./data/`.

Want to expose on the network? Set `BIND_HOST=0.0.0.0` in `.env`
(consider setting `GRUNDSCHUTZ_AUTH_TOKEN` then). Better still:
front it with a real reverse proxy and keep `BIND_HOST` on
loopback.

### B) Behind an existing Traefik

If you already run Traefik with a Docker provider and an HTTPS
entrypoint, the overlay file plugs the service in with no manual
nginx-rule writing:

```bash
# Required in .env for this mode
PUBLIC_HOST=grundschutz.example.com
PROXY_NETWORK=web                  # name of your Traefik network
TRAEFIK_ENTRYPOINT=websecure
TRAEFIK_CERTRESOLVER=letsencrypt

make docker-up-traefik
# = docker compose -f compose.yml -f compose.traefik.yml up -d --build
curl https://$PUBLIC_HOST/health
```

Adds: HTTPS routing on `$PUBLIC_HOST`, a per-client-IP rate-limit
middleware (10 r/s avg, burst 30), the right Host-header allowlist
for the MCP SDK's DNS-rebinding protection. Drops the host-bound
port from mode A (the container is only reachable internally via
the proxy network).

### C) Local development without Docker

```bash
make venv          # creates .venv, installs editable package + dev deps
make build         # builds the SQLite DB (fast, no embeddings yet)
make embed         # ~13 min on Apple Silicon, downloads bge-m3
make serve         # starts the server on 127.0.0.1:8080
make test          # runs pytest (79 tests)
```

> **Note on data**: this repository ships no BSI content - both
> source files (`XML_Kompendium_2023.xml` and `krt2023.xlsx`) are
> downloaded from bsi.bund.de at setup time. See [NOTICE.md](NOTICE.md)
> for licensing details.

---

## Client setup

The MCP protocol is the same everywhere, but the various Anthropic
products differ in how they accept custom remote-server URLs and auth
credentials.

### Claude Desktop (macOS / Windows)

Claude Desktop's "Add custom connector" UI currently does **not**
support static `Authorization: Bearer` headers (Anthropic
[issue #112](https://github.com/anthropics/claude-ai-mcp/issues/112)
closed as "not planned"). The pragmatic path is the `mcp-remote`
stdio↔HTTP bridge.

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "grundschutz": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://grundschutz.example.com/mcp"]
    }
  }
}
```

Add `"--header", "Authorization: Bearer YOURTOKEN"` to `args` if you
enabled `GRUNDSCHUTZ_AUTH_TOKEN`. Then quit Claude Desktop fully
(Cmd+Q) and relaunch.

### Cowork / claude.ai (Web)

Settings → Connectors → Add custom connector:

- Name: `Grundschutz`
- Remote MCP server URL: `https://grundschutz.example.com/mcp`
- Leave OAuth fields empty

Cowork connects from Anthropic's cloud rather than from your machine,
so a static bearer-token header can't reach your server from there.
Run without auth (default) or implement OAuth 2.1 on the server.

---

## Configuration

All variables can be set in `.env`:

| Variable | Default | Notes |
|---|---|---|
| `PUBLIC_HOST` | — (required) | The FQDN behind which the server is reached. |
| `PROXY_NETWORK` | `web` | Docker network your reverse proxy listens on. |
| `TRAEFIK_ENTRYPOINT` | `websecure` | Traefik HTTPS entrypoint name. |
| `TRAEFIK_CERTRESOLVER` | `letsencrypt` | Cert resolver name in your Traefik config. |
| `TRAEFIK_MIDDLEWARES` | `grundschutz-ratelimit` | Comma-separated list. Append your own shared ones if any. |
| `GRUNDSCHUTZ_AUTH_TOKEN` | (empty) | If set, server requires `Authorization: Bearer <value>`. |
| `GRUNDSCHUTZ_LOG_LEVEL` | `WARNING` | INFO during debugging. |
| `GRUNDSCHUTZ_CPU_LIMIT` | `4.0` | Container CPU cap (cores). |
| `GRUNDSCHUTZ_MEMORY_LIMIT` | `4G` | Container memory cap. |
| `GRUNDSCHUTZ_ALLOWED_HOSTS` | (derived) | MCP DNS-rebinding allowlist. Defaults derived from `PUBLIC_HOST`. |
| `GRUNDSCHUTZ_SEMANTIC` | `1` | Set `0` to skip loading `sqlite-vec`. |

---

## Updating to a new BSI edition

When BSI publishes a new edition (typically yearly):

```bash
rm -f XML_Kompendium_*.xml krt*.xlsx       # drops the cached sources
$EDITOR scripts/fetch-bsi-xml.sh           # update URL + filename for the new year
$EDITOR scripts/fetch-bsi-krt.sh           # likewise for KRT
make fetch-xml fetch-krt
docker compose run --rm grundschutz-mcp rebuild   # rebuild DB + re-embed
```

The DocBook XML and the KRT XLSX may carry the occasional
copy-paste quirk (BSI publishes corrections out-of-band). Add
codes to ``TYPO_FIXES`` in ``src/grundschutz_mcp/ingest/xml_importer.py``
when you find one, and to the role-name normalisation map there if
new short forms appear.

---

## Tests

`make test` runs the full test suite (79 tests):

- XML importer (synthetic DocBook fragment + real-data smoketest)
- KRT importer (synthetic openpyxl-built fixture + real-data smoketest)
- Cross-reference extractor
- FTS5 smoketests (Backup / Protokollierung / "Verschlusselung" with diacritic folding)
- Embedding pipeline with a mocked SentenceTransformer
- All 16 MCP tools
- HTTP server (auth, healthcheck)
- End-to-end through the streamable-http transport

Real-data smoketests skip automatically if the source files aren't
present, so CI works without internet access or BSI downloads.

---

## Out of scope / planned

- Grundschutz++ (OSCAL) import — the schema is ready, the ingest module isn't yet.
- Web UI / admin interface.
- Cross-mapping to ISO 27001, NIST CSF, etc.
- OAuth 2.1 server (currently rely on optional bearer or reverse-proxy auth).

---

## License & attribution

This project is released under the [MIT license](LICENSE).

**BSI IT-Grundschutz-Kompendium**: © Bundesamt für Sicherheit in der
Informationstechnik (BSI). The content is published for free
re-use for training and information purposes under the BSI's licence;
commercial use requires a separate agreement with the BSI. See
[NOTICE.md](NOTICE.md) for details.

**[BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)**: the multilingual
embedding model. MIT.

**[Model Context Protocol](https://modelcontextprotocol.io/)**: the
spec and Python SDK powering the server transport.
