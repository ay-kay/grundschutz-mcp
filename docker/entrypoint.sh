#!/usr/bin/env bash
# ============================================================================
# grundschutz-mcp container entrypoint
# ----------------------------------------------------------------------------
# Steps on first start:
#   1. If the SQLite DB is missing, run the XML+KRT+crossref build.
#   2. If no embeddings exist yet, download the model and embed everything.
#      Subsequent starts skip both - the model is cached in /app/hf-cache,
#      the DB in /app/db. Both should be on a host volume.
#   3. Hand off to the server.
#
# Pass arbitrary arguments to run one-off commands inside the container:
#   docker compose run --rm grundschutz-mcp rebuild       # full rebuild
#   docker compose run --rm grundschutz-mcp embed         # only re-embed
#   docker compose run --rm grundschutz-mcp bash          # shell
# ============================================================================
set -euo pipefail

DB="${GRUNDSCHUTZ_DB:-/app/db/grundschutz.db}"
EMBEDDING_MODEL="${GRUNDSCHUTZ_EMBEDDING_MODEL:-BAAI/bge-m3}"

ensure_db() {
    if [ ! -s "$DB" ]; then
        echo "[entrypoint] no DB at $DB - running full build..."
        python -m grundschutz_mcp.build all --db "$DB"
    fi
}

ensure_embeddings() {
    local has
    has=$(python - <<PY
import sqlite3
n = sqlite3.connect("$DB").execute(
    "SELECT COUNT(*) FROM embedding_meta"
).fetchone()[0]
print(n)
PY
)
    if [ "$has" = "0" ]; then
        echo "[entrypoint] no embeddings yet - downloading $EMBEDDING_MODEL"
        echo "             and embedding every requirement, module and threat."
        echo "             (first run only; ~2.3 GB download + 15-120 min CPU)"
        python -m grundschutz_mcp.build embed --db "$DB" \
            --embedding-model "$EMBEDDING_MODEL"
    fi
}

case "${1:-serve}" in
    serve)
        ensure_db
        ensure_embeddings
        echo "[entrypoint] starting MCP server on ${GRUNDSCHUTZ_HOST}:${GRUNDSCHUTZ_PORT}"
        exec python -m grundschutz_mcp.server
        ;;
    rebuild)
        rm -f "$DB" "${DB}-wal" "${DB}-shm"
        ensure_db
        ensure_embeddings
        ;;
    schema|xml|krt|crossref|embed|all|all-with-embeddings)
        exec python -m grundschutz_mcp.build "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
