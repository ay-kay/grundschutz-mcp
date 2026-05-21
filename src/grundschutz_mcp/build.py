"""Command-line entrypoint for building the grundschutz database.

Pipeline order:

    schema   -> empty SQLite DB with our tables, FTS triggers, etc.
    xml      -> import structure + prose from the BSI DocBook XML
                (layers, modules, requirements, threats, roles, R1/R2/R3,
                 specific threat scenarios, full prose)
    krt      -> import requirement<->threat M:N + per-link C/I/A goals
                from the BSI Kreuzreferenztabelle XLSX
    crossref -> regex-extract cross-references from requirement prose
    embed    -> compute dense embeddings via bge-m3 (heavy: 2.3 GB model
                download + 60-120 min CPU on x86 / ~13 min on Apple MPS)

Usage examples (also driven by the project Makefile)::

    python -m grundschutz_mcp.build schema
    python -m grundschutz_mcp.build xml
    python -m grundschutz_mcp.build krt
    python -m grundschutz_mcp.build crossref
    python -m grundschutz_mcp.build embed
    python -m grundschutz_mcp.build all                 # schema..crossref
    python -m grundschutz_mcp.build all-with-embeddings # plus embed
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import structlog

from .db import apply_schema, connect
from .ingest.crossref_extractor import extract_cross_references
from .ingest.krt_importer import import_krt
from .ingest.xml_importer import import_xml

log = structlog.get_logger(__name__)


# Default paths assume the project layout documented in the top-level README.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "db" / "grundschutz.db"
DEFAULT_XML = PROJECT_ROOT / "XML_Kompendium_2023.xml"
DEFAULT_KRT = PROJECT_ROOT / "krt2023.xlsx"


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grundschutz-build")
    p.add_argument(
        "step",
        choices=(
            "schema",
            "xml",
            "krt",
            "crossref",
            "embed",
            "all",
            "all-with-embeddings",
        ),
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--xml", type=Path, default=DEFAULT_XML)
    p.add_argument("--krt", type=Path, default=DEFAULT_KRT)
    p.add_argument(
        # bge-m3 has 8192-token context, which matters for Bausteine
        # (median ~2500 tokens, max ~5400). e5-large's 512-token limit
        # would silently truncate ~80 % of module text.
        "--embedding-model",
        default="BAAI/bge-m3",
    )
    p.add_argument("--embedding-dim", type=int, default=1024)
    # 8 keeps memory under control for 8 k-context models on MPS;
    # raise it for short-context models like e5-large (try 32).
    p.add_argument("--embedding-batch-size", type=int, default=8)
    return p


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def _drop_existing(db_path: Path) -> None:
    if db_path.exists():
        log.info("removing_existing_db", path=str(db_path))
        db_path.unlink()


def _do_schema(db_path: Path) -> None:
    _drop_existing(db_path)
    conn = connect(db_path)
    apply_schema(conn)
    conn.close()
    log.info("schema_applied", path=str(db_path))


def _do_xml(db_path: Path, xml_path: Path) -> None:
    conn = connect(db_path)
    import_xml(conn, xml_path)
    conn.close()


def _do_krt(db_path: Path, krt_path: Path) -> None:
    conn = connect(db_path)
    import_krt(conn, krt_path)
    conn.close()


def _do_crossref(db_path: Path) -> None:
    conn = connect(db_path)
    extract_cross_references(conn)
    conn.close()


def _do_embed(args: argparse.Namespace) -> None:
    from .ingest.embeddings import generate_embeddings  # heavy import

    conn = connect(args.db, load_vec=True)
    generate_embeddings(
        conn,
        model_name=args.embedding_model,
        dimensions=args.embedding_dim,
        batch_size=args.embedding_batch_size,
    )
    conn.close()


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = _build_argparser().parse_args(argv)
    step = args.step

    if step == "schema":
        _do_schema(args.db)
    elif step == "xml":
        _do_xml(args.db, args.xml)
    elif step == "krt":
        _do_krt(args.db, args.krt)
    elif step == "crossref":
        _do_crossref(args.db)
    elif step == "embed":
        _do_embed(args)
    elif step in ("all", "all-with-embeddings"):
        _do_schema(args.db)
        _do_xml(args.db, args.xml)
        _do_krt(args.db, args.krt)
        _do_crossref(args.db)
        if step == "all-with-embeddings":
            _do_embed(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
