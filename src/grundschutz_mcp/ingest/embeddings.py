"""Generate dense vector embeddings for requirements, modules and threats.

Final step of the ingest pipeline. Uses ``sentence-transformers`` for
local inference (no cloud APIs) and ``sqlite-vec`` for storage.

The model name is configurable; the default ``BAAI/bge-m3`` produces
1024-dimensional vectors, has an 8192-token context (covers full
Baustein text without truncation) and handles German well. The E5
family (``intfloat/multilingual-e5-*``) is also supported via the
``prefixes_for`` helper. We embed:

* every requirement, as ``title + prose``
* every module, as ``title + description + per-Baustein threat scenarios``
* every threat, as ``title + description``

The current model is recorded in ``embedding_meta``. Re-embedding with
a different model clears the vector tables and inserts a new metadata
row, so a subsequent query knows which model produced the stored
vectors and can encode itself the matching way.

This module is intentionally lazy about imports: importing it does
NOT pull in torch/sentence-transformers. The heavy dependency is only
loaded when ``generate_embeddings`` is actually called.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

log = structlog.get_logger(__name__)

DEFAULT_MODEL = "BAAI/bge-m3"
DEFAULT_DIM = 1024
# bge-m3 supports 8192-token sequences, so a batch of 32 can demand >20 GiB
# during attention computation. 8 fits comfortably even on a 16 GB MPS box
# while still amortising kernel launch overhead. Override per-run via
# --embedding-batch-size for short-context models like e5 (which can use 32+).
DEFAULT_BATCH_SIZE = 8


def prefixes_for(model_name: str) -> tuple[str, str]:
    """Return ``(passage_prefix, query_prefix)`` for the given embedding model.

    The E5 family (``intfloat/multilingual-e5-*``) requires explicit
    ``passage: `` / ``query: `` prefixes for retrieval; using the wrong
    prefix - or none at all - measurably degrades quality. Most other
    modern multilingual models (BAAI/bge-m3, jinaai/jina-embeddings-v3)
    take plain text.
    """
    if "e5" in model_name.lower():
        return ("passage: ", "query: ")
    return ("", "")


@dataclass
class EmbeddingStats:
    model_name: str
    dimensions: int
    requirements_embedded: int = 0
    modules_embedded: int = 0
    threats_embedded: int = 0

    def as_dict(self) -> dict[str, object]:
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# Schema bootstrap for the vec0 virtual tables. We create them lazily here
# rather than at apply_schema time so the base schema stays loadable without
# the sqlite-vec extension.
# ---------------------------------------------------------------------------


def ensure_vec_tables(conn: sqlite3.Connection, dimensions: int) -> None:
    """Create requirement_vec / module_vec / threat_vec if missing.

    Requires that the connection was opened with ``load_vec=True``.
    """
    for table in ("requirement_vec", "module_vec", "threat_vec"):
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0("
            f"  id INTEGER PRIMARY KEY, embedding FLOAT[{dimensions}]"
            f")"
        )


def _clear_vec_tables(conn: sqlite3.Connection) -> None:
    for table in ("requirement_vec", "module_vec", "threat_vec"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            # table doesn't exist yet - fine
            pass


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_embeddings(
    conn: sqlite3.Connection,
    model_name: str = DEFAULT_MODEL,
    dimensions: int = DEFAULT_DIM,
    batch_size: int = DEFAULT_BATCH_SIZE,
    catalog_name: str = "BSI IT-Grundschutz 200-2",
    edition: str = "2023",
) -> EmbeddingStats:
    """Embed every requirement, module, threat and store the vectors.

    The connection must have been opened with ``load_vec=True``.
    """
    from sentence_transformers import SentenceTransformer  # heavy import

    catalog_row = conn.execute(
        "SELECT id FROM catalog WHERE name = ? AND edition = ?",
        (catalog_name, edition),
    ).fetchone()
    if catalog_row is None:
        raise RuntimeError(f"No catalog row for name={catalog_name!r} edition={edition!r}")
    catalog_id = catalog_row["id"]

    log.info("loading_embedding_model", model=model_name)
    model = SentenceTransformer(model_name)
    # Sanity-check the dimensionality.
    actual_dim = model.get_sentence_embedding_dimension()
    if actual_dim != dimensions:
        log.warning(
            "model_dim_mismatch",
            configured=dimensions,
            actual=actual_dim,
            note="using actual dimension from model",
        )
        dimensions = actual_dim

    ensure_vec_tables(conn, dimensions)
    stats = EmbeddingStats(model_name=model_name, dimensions=dimensions)

    try:
        with conn:
            _clear_vec_tables(conn)
            _record_meta(conn, model_name, dimensions)

            stats.requirements_embedded = _embed_table(
                conn=conn,
                model=model,
                model_name=model_name,
                batch_size=batch_size,
                fetch_sql=(
                    "SELECT id, title, COALESCE(prose, '') AS body "
                    "FROM requirement WHERE catalog_id = ? "
                    "ORDER BY id"
                ),
                args=(catalog_id,),
                target_table="requirement_vec",
                kind="requirement",
            )
            stats.modules_embedded = _embed_table(
                conn=conn,
                model=model,
                model_name=model_name,
                batch_size=batch_size,
                # The body is description + every specific threat title and
                # description, concatenated. This lets semantic module search
                # surface CON.3 when the user asks about "Ransomware" or
                # "Datensicherungsgeschwindigkeit" - both are CON.3's
                # baustein-specific scenarios, not the catalogue's elementary
                # threats.
                fetch_sql=(
                    "SELECT m.id AS id, m.title AS title, "
                    "       COALESCE(m.description, '') || "
                    "       COALESCE(("
                    "           SELECT char(10) || GROUP_CONCAT("
                    "               mst.title || ': ' || COALESCE(mst.description, ''), char(10)) "
                    "           FROM module_specific_threat mst "
                    "           WHERE mst.module_id = m.id"
                    "       ), '') AS body "
                    "FROM module m WHERE m.catalog_id = ? "
                    "ORDER BY m.id"
                ),
                args=(catalog_id,),
                target_table="module_vec",
                kind="module",
            )
            stats.threats_embedded = _embed_table(
                conn=conn,
                model=model,
                model_name=model_name,
                batch_size=batch_size,
                fetch_sql=(
                    "SELECT id, title, COALESCE(description, '') AS body "
                    "FROM threat WHERE catalog_id = ? "
                    "ORDER BY id"
                ),
                args=(catalog_id,),
                target_table="threat_vec",
                kind="threat",
            )
    except Exception:
        log.exception("embedding_generation_failed")
        raise

    log.info("embedding_generation_done", **stats.as_dict())
    return stats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_meta(
    conn: sqlite3.Connection,
    model_name: str,
    dimensions: int,
) -> None:
    conn.execute(
        "INSERT INTO embedding_meta (model_name, dimensions, created_at) VALUES (?, ?, ?)",
        (model_name, dimensions, datetime.now(UTC).isoformat(timespec="seconds")),
    )


def _embed_table(
    *,
    conn: sqlite3.Connection,
    model,
    model_name: str,
    batch_size: int,
    fetch_sql: str,
    args: Sequence,
    target_table: str,
    kind: str,
) -> int:
    """Stream rows in batches, encode, insert into ``target_table``."""
    rows = conn.execute(fetch_sql, args).fetchall()
    ids: list[int] = [row["id"] for row in rows]
    passage_prefix, _ = prefixes_for(model_name)
    inputs: list[str] = [_compose_text(passage_prefix, row) for row in rows]
    if not inputs:
        return 0

    log.info("embedding_start", kind=kind, n=len(inputs))
    inserted = 0
    for chunk_start in range(0, len(inputs), batch_size):
        chunk_inputs = inputs[chunk_start : chunk_start + batch_size]
        chunk_ids = ids[chunk_start : chunk_start + batch_size]
        vectors = model.encode(
            chunk_inputs,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        for entity_id, vec in zip(chunk_ids, vectors, strict=True):
            # sqlite-vec accepts a binary buffer of float32 values.
            conn.execute(
                f"INSERT INTO {target_table} (id, embedding) VALUES (?, ?)",
                (entity_id, vec.astype("float32").tobytes()),
            )
            inserted += 1
    log.info("embedding_done", kind=kind, n=inserted)
    return inserted


def _compose_text(passage_prefix: str, row: sqlite3.Row) -> str:
    """Combine title and body for one row, prepending the model-specific prefix.

    ``passage_prefix`` comes from :func:`prefixes_for` and is the empty
    string for plain-text models (bge-m3, jina-v3) or ``"passage: "`` for
    the E5 family.
    """
    title = (row["title"] or "").strip()
    body = (row["body"] or "").strip()
    composed = title if not body else f"{title}\n{body}"
    return f"{passage_prefix}{composed}" if passage_prefix else composed
