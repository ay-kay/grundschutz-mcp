# syntax=docker/dockerfile:1.7
# ============================================================================
# grundschutz-mcp - production Docker image
# ----------------------------------------------------------------------------
# Multi-stage build:
#   * builder: installs torch / sentence-transformers / sqlite-vec into venv
#   * runtime: slim image that copies the venv + project source + BSI data
#
# The bge-m3 model is NOT baked into the image (saves ~2.3 GB). It is
# downloaded into a Hugging Face cache volume on first container start.
# The SQLite DB is also built on first start by entrypoint.sh.
#
# Prerequisites for `docker build` (run from the project root):
#   make fetch-xml     # downloads XML_Kompendium_2023.xml from bsi.bund.de
#   make fetch-krt     # downloads krt2023.xlsx from bsi.bund.de
#
# Both produce gitignored files that the COPY directives below pick up.
# ============================================================================

FROM python:3.12-slim-bookworm AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -e ".[server,embeddings]"

# ----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    GRUNDSCHUTZ_HOST=0.0.0.0 \
    GRUNDSCHUTZ_PORT=8080 \
    GRUNDSCHUTZ_DB=/app/db/grundschutz.db \
    HF_HOME=/app/hf-cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY pyproject.toml ./
COPY src/ ./src/
COPY schema/ ./schema/
COPY XML_Kompendium_2023.xml krt2023.xlsx ./
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh \
    && mkdir -p /app/db /app/hf-cache

EXPOSE 8080
# start-period covers the first-start bootstrap: HuggingFace model
# download (~2 min) and catalog embedding (~15 min on Apple Silicon MPS,
# up to ~120 min on x86 CPU). During this window, failures don't mark
# the container unhealthy and `docker ps` shows "starting" instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120m --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["serve"]
