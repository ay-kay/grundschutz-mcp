"""Search tools: FTS5 keyword search, sqlite-vec semantic search, hybrid RRF.

The hybrid tool is the recommended default for agents - dense retrieval
exhibits term-bias on rare proper nouns (product names, malware families,
BSI codes), while FTS5 is brittle on paraphrased questions. Reciprocal
Rank Fusion (RRF) takes the best of both with no extra model.
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from typing import Any

from ._common import ENTITY_TYPES

# Default RRF constant. The seminal paper (Cormack et al., 2009) uses 60.
RRF_K = 60


def search_keyword(
    conn: sqlite3.Connection,
    query: str,
    entity_types: list[str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Run an FTS5 keyword search across requirements / modules / threats.

    Returns a flat list of hits, each tagged with ``entity_type`` and the
    raw FTS rank (lower = better). The query string is forwarded to FTS5
    as-is, so callers can use FTS5 operators (``AND``, ``NEAR``, quoting).
    """
    if not query.strip():
        return {"query": query, "hits": []}
    types = _validate_entity_types(entity_types)

    hits: list[dict[str, Any]] = []
    for et in types:
        table_fts = f"{et}_fts"
        source = et  # requirement_fts <-> requirement
        try:
            rows = conn.execute(
                f"SELECT s.code AS code, s.title AS title, "
                f"       bm25({table_fts}) AS score "
                f"FROM {table_fts} f "
                f"JOIN {source} s ON s.id = f.rowid "
                f"WHERE {table_fts} MATCH ? "
                f"ORDER BY score "
                f"LIMIT ?",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Raw query contained FTS5-incompatible punctuation (e.g. "?")
            # or stray operators. Fall back to a safe tokenised OR-query.
            safe = _to_or_query(query)
            if not safe:
                continue
            rows = conn.execute(
                f"SELECT s.code AS code, s.title AS title, "
                f"       bm25({table_fts}) AS score "
                f"FROM {table_fts} f "
                f"JOIN {source} s ON s.id = f.rowid "
                f"WHERE {table_fts} MATCH ? "
                f"ORDER BY score "
                f"LIMIT ?",
                (safe, limit),
            ).fetchall()

        # For modules, also search the baustein-specific threat scenarios.
        # A hit there (e.g. "Ransomware" inside CON.3's specific threats) is
        # promoted to a hit on the parent module so the user finds the
        # Baustein they care about.
        if et == "module":
            rows = list(rows)
            seen = {r["code"] for r in rows}
            mst_rows = _module_specific_threat_keyword_hits(conn, query, limit)
            for r in mst_rows:
                if r["code"] not in seen:
                    rows.append(r)
                    seen.add(r["code"])
        for r in rows:
            hits.append(
                {
                    "entity_type": et,
                    "code": r["code"],
                    "title": r["title"],
                    "score": r["score"],
                }
            )

    # Re-sort across entity types by score so callers can paginate uniformly.
    hits.sort(key=lambda h: h["score"])
    return {"query": query, "hits": hits[:limit]}


def search_semantic(
    conn: sqlite3.Connection,
    query: str,
    entity_types: list[str] | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Vector search over the requirement / module / threat embeddings.

    Requires the connection to have been opened with ``load_vec=True`` and
    the embedding pipeline to have populated the ``*_vec`` tables.
    """
    if not query.strip():
        return {"query": query, "hits": []}
    types = _validate_entity_types(entity_types)

    try:
        model_name, model = _load_embedding_model(conn)
    except RuntimeError as exc:
        # Embedding pipeline not initialised. We return an empty result
        # plus a structured ``error`` field so callers (in particular
        # search_hybrid) can degrade gracefully instead of crashing.
        return {"query": query, "hits": [], "error": str(exc)}
    qvec = _encode_query(model, model_name, query)

    hits: list[dict[str, Any]] = []
    for et in types:
        vec_table = f"{et}_vec"
        source = et
        try:
            rows = conn.execute(
                f"SELECT s.code AS code, s.title AS title, v.distance AS distance "
                f"FROM {vec_table} v "
                f"JOIN {source} s ON s.id = v.id "
                f"WHERE v.embedding MATCH ? "
                f"  AND k = ? "
                f"ORDER BY distance",
                (qvec, top_k),
            )
        except sqlite3.OperationalError as exc:
            return {
                "query": query,
                "hits": [],
                "error": f"Vector table {vec_table} not available: {exc}. "
                "Run the embedding generator first.",
            }
        for r in rows:
            # Cosine distance: 0 = identical, 2 = opposite. We report a
            # similarity score (1 - distance/2) clipped to [0, 1] so it's
            # easier to reason about as "relevance".
            sim = max(0.0, 1.0 - r["distance"] / 2.0)
            hits.append(
                {
                    "entity_type": et,
                    "code": r["code"],
                    "title": r["title"],
                    "score": sim,
                    "distance": r["distance"],
                }
            )

    hits.sort(key=lambda h: -h["score"])
    return {"query": query, "hits": hits[:top_k]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_entity_types(types: list[str] | None) -> list[str]:
    if types is None:
        return list(ENTITY_TYPES)
    bad = [t for t in types if t not in ENTITY_TYPES]
    if bad:
        raise ValueError(f"unknown entity_types: {bad}. Allowed: {list(ENTITY_TYPES)}")
    return types


@lru_cache(maxsize=1)
def _load_embedding_model_cached(model_name: str):
    """Lazy, cached load of the SentenceTransformer model."""
    from sentence_transformers import SentenceTransformer  # heavy import

    return SentenceTransformer(model_name)


def _load_embedding_model(conn: sqlite3.Connection) -> tuple[str, object]:
    """Look up the model name from ``embedding_meta`` and load it.

    Returns ``(model_name, model)`` so callers can pick the right inference
    conventions (prefix vs. plain text) for the model that produced the
    stored vectors.
    """
    row = conn.execute("SELECT model_name FROM embedding_meta ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("embedding_meta is empty. Run the embedding generator first.")
    name = row["model_name"]
    return name, _load_embedding_model_cached(name)


def search_hybrid(
    conn: sqlite3.Connection,
    query: str,
    entity_types: list[str] | None = None,
    limit: int = 20,
    pool_size: int | None = None,
) -> dict[str, Any]:
    """Hybrid keyword + semantic search using Reciprocal Rank Fusion.

    Runs FTS5 and vec0 in parallel, then fuses the rankings with
    ``score = Σ 1 / (RRF_K + rank_i)``. This compensates the well-known
    weakness of dense retrieval on rare named entities ("Ransomware",
    "APP.4.6", "SAP ABAP") while keeping semantic recall on phrased
    questions ("Wie schütze ich gegen Erpressung?").

    The returned ``rrf_score`` is a small absolute number; treat it as an
    ordering signal, not as a probability. The ``keyword_rank`` and
    ``semantic_rank`` fields tell the caller which retriever produced
    each hit (either can be ``None`` if only one retriever saw it).

    If the embedding pipeline isn't available (vec0 missing or no
    embedding_meta row), the function falls back to FTS-only.
    """
    if not query.strip():
        return {"query": query, "hits": []}
    types = _validate_entity_types(entity_types)
    pool = pool_size or max(limit * 3, 60)

    # Keyword side: FTS5 tolerates phrased natural language poorly because
    # tokens are implicit-ANDed. We OR the tokens so any of them can match.
    kw_query = _to_or_query(query)
    kw_hits = (
        search_keyword(conn, kw_query, entity_types=types, limit=pool)["hits"] if kw_query else []
    )

    # Semantic side. May fail gracefully if vec extension or embeddings
    # are missing; that's not a hard error in hybrid mode.
    sem_payload = search_semantic(conn, query, entity_types=types, top_k=pool)
    sem_hits = sem_payload.get("hits", [])
    sem_error = sem_payload.get("error")

    # Build (entity_type, code) -> rank dictionaries (1-based ranks).
    kw_ranks: dict[tuple[str, str], int] = {
        (h["entity_type"], h["code"]): i + 1 for i, h in enumerate(kw_hits)
    }
    sem_ranks: dict[tuple[str, str], int] = {
        (h["entity_type"], h["code"]): i + 1 for i, h in enumerate(sem_hits)
    }

    # RRF fusion.
    keys = set(kw_ranks) | set(sem_ranks)
    titles: dict[tuple[str, str], str] = {}
    for h in kw_hits + sem_hits:
        titles[(h["entity_type"], h["code"])] = h["title"]
    fused = []
    for key in keys:
        rrf = 0.0
        if key in kw_ranks:
            rrf += 1.0 / (RRF_K + kw_ranks[key])
        if key in sem_ranks:
            rrf += 1.0 / (RRF_K + sem_ranks[key])
        fused.append(
            {
                "entity_type": key[0],
                "code": key[1],
                "title": titles.get(key, ""),
                "rrf_score": rrf,
                "keyword_rank": kw_ranks.get(key),
                "semantic_rank": sem_ranks.get(key),
            }
        )

    fused.sort(key=lambda h: -h["rrf_score"])
    out: dict[str, Any] = {"query": query, "hits": fused[:limit]}
    if sem_error:
        out["note"] = f"Semantic search unavailable, keyword-only results returned: {sem_error}"
    return out


# Tokens we don't want to pass to FTS5 because they collide with operators
# or because they are typical German stopwords that don't help retrieval.
_FTS_STOPWORDS = {
    "and",
    "or",
    "not",
    "near",
    "the",
    "der",
    "die",
    "das",
    "und",
    "oder",
    "nicht",
    "ein",
    "eine",
    "einer",
    "von",
    "zu",
    "mit",
    "bei",
    "auf",
    "für",
    "im",
    "in",
    "vor",
    "nach",
    "ich",
    "du",
    "wir",
    "ihr",
    "sie",
    "es",
    "ist",
    "sind",
    "war",
    "wer",
    "was",
    "wie",
    "wo",
    "warum",
    "welche",
    "welcher",
    "welches",
}


def _module_specific_threat_keyword_hits(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    """FTS lookup over baustein-specific threats, projected to the module.

    Returns dicts shaped like the module FTS query (``code``, ``title``,
    ``score``) so callers can merge them with module results. We dedupe
    in Python because FTS5's ``bm25()`` auxiliary function cannot be used
    inside SQL aggregates / ``GROUP BY``.
    """
    raw_rows: list[sqlite3.Row] = []
    for q in (query, _to_or_query(query)):
        if not q:
            continue
        try:
            raw_rows = list(
                conn.execute(
                    "SELECT m.code AS code, m.title AS title, "
                    "       bm25(module_specific_threat_fts) AS score "
                    "FROM module_specific_threat_fts f "
                    "JOIN module_specific_threat mst ON mst.id = f.rowid "
                    "JOIN module m ON m.id = mst.module_id "
                    "WHERE module_specific_threat_fts MATCH ? "
                    "ORDER BY score "
                    "LIMIT ?",
                    (q, limit * 4),  # pool extra so dedup gives us ``limit``
                ).fetchall()
            )
            break
        except sqlite3.OperationalError:
            continue

    # Keep the best score per module code.
    best: dict[str, dict[str, Any]] = {}
    for r in raw_rows:
        if r["code"] not in best or r["score"] < best[r["code"]]["score"]:
            best[r["code"]] = {
                "code": r["code"],
                "title": r["title"],
                "score": r["score"],
            }
    return sorted(best.values(), key=lambda h: h["score"])[:limit]


def _to_or_query(natural: str) -> str:
    """Turn a natural-language query into an FTS5 OR-of-tokens string.

    Keeps tokens with diacritics intact (the unicode61 tokenizer folds them
    at index time). Strips stopwords and FTS5 reserved operators. Each
    token is wrapped in double quotes to neutralise stray punctuation.
    """
    raw = re.findall(r"[\wÄÖÜäöüß]+", natural, flags=re.UNICODE)
    tokens = [t for t in raw if len(t) >= 2 and t.lower() not in _FTS_STOPWORDS]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def _encode_query(model, model_name: str, query: str) -> bytes:
    """Encode a search query with the prefix that the model was trained for.

    The E5 family expects ``query: <text>``; bge-m3 / jina-v3 want plain text.
    Using the wrong convention noticeably degrades retrieval quality.
    """
    from grundschutz_mcp.ingest.embeddings import prefixes_for

    _, query_prefix = prefixes_for(model_name)
    text = f"{query_prefix}{query}" if query_prefix else query
    vec = model.encode(
        [text],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )[0]
    return vec.astype("float32").tobytes()
