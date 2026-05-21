# Attribution & licensing

## What this repository contains

**Code only.** The Python source, build scripts, schema, Dockerfile,
tests and configuration in this repository are released under the
[MIT license](LICENSE) (© 2026 grundschutz-mcp contributors).

**No BSI-derived content.** The Kompendium catalog (text, structural
relationships, threat mappings) is *not* shipped with this repository.
The build pipeline fetches both source files directly from the BSI at
setup time, into gitignored locations:

| BSI source | Fetched by | Output (gitignored) |
|---|---|---|
| DocBook XML of the Kompendium | `make fetch-xml` | `./XML_Kompendium_2023.xml` |
| Kreuzreferenztabelle XLSX (requirement↔threat + C/I/A) | `make fetch-krt` | `./krt2023.xlsx` |

Both files come straight from `bsi.bund.de`; the build then writes
their contents into a local SQLite DB. Anyone running the pipeline is
responsible for honouring the BSI terms below.

## BSI IT-Grundschutz-Kompendium

© Bundesamt für Sicherheit in der Informationstechnik (BSI).

Per the BSI's general [Nutzungsbedingungen](https://www.bsi.bund.de/DE/Service/Nutzungsbedingungen/Nutzungsbedingungen_node.html):

* **Non-commercial use** that serves IT security is permitted without a
  separate licence agreement, on condition that the source is
  attributed in any reproduction.
* **Commercial use** — including advertising or productisation —
  requires a separate licence agreement with the BSI
  ([it-grundschutz@bsi.bund.de](mailto:it-grundschutz@bsi.bund.de)).

If you intend to deploy this MCP server in a commercial setting
(consulting engagement, paid SaaS, internal-but-revenue-relevant
infrastructure, …) please consult the BSI directly. The MIT licence on
this code does **not** grant any rights in the BSI content the code
processes.

Source URLs used by the build pipeline:

* XML: `https://www.bsi.bund.de/SharedDocs/Downloads/DE/BSI/Grundschutz/IT-GS-Kompendium/XML_Kompendium_2023.xml?__blob=publicationFile&v=4`
* KRT: `https://www.bsi.bund.de/SharedDocs/Downloads/DE/BSI/Grundschutz/IT-GS-Kompendium/krt2023_Excel.xlsx?__blob=publicationFile&v=4`

## Models

### BAAI/bge-m3
- Source: <https://huggingface.co/BAAI/bge-m3>
- License: MIT
- Role: 1024-dimensional multilingual embedding model used for
  semantic search. Downloaded on first start of the embedding pipeline
  into a host-mounted cache volume; not redistributed by this repo.

## Code dependencies

Notable runtime dependencies (full tree in `pyproject.toml`):

| Package | License | Role |
|---|---|---|
| [mcp](https://github.com/modelcontextprotocol/python-sdk) | MIT | Model Context Protocol SDK |
| [sqlite-vec](https://github.com/asg017/sqlite-vec) | Apache-2.0 / MIT | SQLite vector extension |
| [sentence-transformers](https://github.com/UKPLab/sentence-transformers) | Apache-2.0 | Local embedding inference |
| [openpyxl](https://foss.heptapod.net/openpyxl/openpyxl) | MIT | KRT XLSX parsing |
| [structlog](https://www.structlog.org/) | Apache-2.0 / MIT | Structured logging |
| [starlette](https://www.starlette.io/) | BSD-3-Clause | ASGI framework |
| [uvicorn](https://www.uvicorn.org/) | BSD-3-Clause | ASGI server |
