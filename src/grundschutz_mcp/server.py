"""MCP server entrypoint.

Exposes the 14 grundschutz tools over HTTP using the MCP SDK's
``streamable-http`` transport. Authentication is a single shared bearer
token loaded from the ``GRUNDSCHUTZ_AUTH_TOKEN`` env var; when unset, the
server runs unauthenticated (useful for local development).

Healthcheck at ``GET /health`` for monitoring.

Run with::

    python -m grundschutz_mcp.server
    # or via the project Makefile:
    make serve
"""

from __future__ import annotations

import hmac
import logging
import sqlite3
import threading
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .config import ServerConfig
from .tools import modules as mod_tools
from .tools import requirements as req_tools
from .tools import search as search_tools
from .tools import threats as threat_tools
from .tools._common import CodeNotFoundError

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# DB connection management
# ---------------------------------------------------------------------------


class _DbHolder:
    """Holds a single SQLite connection used by every tool call.

    SQLite with WAL handles concurrent readers fine, and our workload is
    read-mostly. We open the connection with ``check_same_thread=False`` so
    Starlette's worker threads can share it.
    """

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def get(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        with self._lock:
            if self._conn is None:
                load_vec = self.cfg.semantic_search_enabled
                conn = sqlite3.connect(
                    str(self.cfg.db_path),
                    check_same_thread=False,
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
                if load_vec:
                    try:
                        import sqlite_vec

                        conn.enable_load_extension(True)
                        sqlite_vec.load(conn)
                        conn.enable_load_extension(False)
                    except Exception as exc:
                        log.warning(
                            "sqlite_vec_unavailable",
                            error=str(exc),
                            note="semantic search will fail until installed",
                        )
                self._conn = conn
        return self._conn


# ---------------------------------------------------------------------------
# Auth middleware (Bearer token)
# ---------------------------------------------------------------------------


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests that don't carry the configured bearer token.

    ``/health`` is exempted so monitoring works without the token.
    The middleware is a no-op when no token is configured.
    """

    def __init__(self, app, expected_token: str | None) -> None:
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):
        if self._expected is None:
            return await call_next(request)
        if request.url.path == "/health":
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse(
                {"error": "missing bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Constant-time comparison to avoid leaking the token byte-by-byte
        # via response-time differences.
        presented = auth.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(presented, self._expected):
            return JSONResponse(
                {"error": "invalid bearer token"},
                status_code=403,
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP, db: _DbHolder) -> None:
    """Bind tool functions to MCP tool names."""

    # --- modules / layers ---

    @mcp.tool()
    def list_layers() -> list[dict[str, str]]:
        """Return all 10 BSI Grundschutz layers (APP, CON, DER, IND, INF, ISMS, NET, OPS, ORP, SYS)."""
        return mod_tools.list_layers(db.get())

    @mcp.tool()
    def list_modules(
        layer: str | None = None,
        search: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List Bausteine, optionally filtered.

        ``priority`` accepts ``R1`` (highest, e.g. ISMS.1 and the ORP layer),
        ``R2`` or ``R3`` and returns only Bausteine in that implementation-
        priority class as defined by Kapitel 2.2 of the BSI Kompendium.
        """
        return mod_tools.list_modules(
            db.get(),
            layer=layer,
            search=search,
            priority=priority,
            limit=limit,
        )

    @mcp.tool()
    def get_module(code: str) -> dict[str, Any]:
        """Return one Baustein with description, threat situation, baustein-specific threat scenarios, and its requirements."""
        try:
            return mod_tools.get_module(db.get(), code)
        except CodeNotFoundError as exc:
            return exc.as_error_response("module")

    @mcp.tool()
    def list_module_threats(code: str) -> dict[str, Any]:
        """Return the baustein-specific named threat scenarios of one Baustein.

        These are the sub-sections of the Baustein's Gefährdungslage in the BSI
        XML (e.g. for CON.3: "Ransomware", "Fehlende Wiederherstellungstests",
        "Ungeeignete Aufbewahrung der Speichermedien" etc.). Not to be
        confused with the 47 elementary threats (G 0.x), which you can fetch
        via get_threats_for_requirement.
        """
        try:
            return mod_tools.list_module_threats(db.get(), code)
        except CodeNotFoundError as exc:
            return exc.as_error_response("module")

    # --- requirements ---

    @mcp.tool()
    def list_requirements(
        module_code: str,
        level: str | None = None,
        include_deprecated: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Return the requirements of one Baustein. Filter by level (Basis|Standard|Hoch)."""
        try:
            return req_tools.list_requirements(
                db.get(),
                module_code=module_code,
                level=level,
                include_deprecated=include_deprecated,
            )
        except CodeNotFoundError as exc:
            return exc.as_error_response("module")

    @mcp.tool()
    def get_requirement(code: str) -> dict[str, Any]:
        """Return one requirement with full prose, module context, and assigned roles."""
        try:
            return req_tools.get_requirement(db.get(), code)
        except CodeNotFoundError as exc:
            return exc.as_error_response("requirement")

    @mcp.tool()
    def get_cross_references(req_code: str) -> dict[str, Any]:
        """Return the codes that a requirement's prose references."""
        try:
            return req_tools.get_cross_references(db.get(), req_code)
        except CodeNotFoundError as exc:
            return exc.as_error_response("requirement")

    # --- threats ---

    @mcp.tool()
    def list_threats() -> list[dict[str, str]]:
        """Return all 47 elementary BSI threats (G 0.1 .. G 0.47)."""
        return threat_tools.list_threats(db.get())

    @mcp.tool()
    def get_threat(code: str) -> dict[str, Any]:
        """Return one elementary threat with its full description."""
        try:
            return threat_tools.get_threat(db.get(), code)
        except CodeNotFoundError as exc:
            return exc.as_error_response("threat")

    @mcp.tool()
    def get_threats_for_requirement(req_code: str) -> dict[str, Any]:
        """Return the threats a requirement addresses, with per-link protection goals (C/I/A)."""
        try:
            return threat_tools.get_threats_for_requirement(db.get(), req_code)
        except CodeNotFoundError as exc:
            return exc.as_error_response("requirement")

    @mcp.tool()
    def get_requirements_for_threat(threat_code: str) -> dict[str, Any]:
        """Return all requirements that address a given elementary threat."""
        try:
            return threat_tools.get_requirements_for_threat(db.get(), threat_code)
        except CodeNotFoundError as exc:
            return exc.as_error_response("threat")

    # --- search ---

    @mcp.tool()
    def search(
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """**Recommended default search.** Hybrid keyword + semantic over the
        BSI catalog via Reciprocal Rank Fusion.

        Use this for almost every search question - it handles both phrased
        natural-language queries ("Wie sichere ich Backups gegen Erpressung?")
        and concrete named entities ("Ransomware", "MFA", "SAP ABAP") well,
        because it fuses FTS5 keyword matching with the dense embedding
        search.

        ``entity_types`` filters to subsets of {"requirement", "module",
        "threat"}; default is all three. Hits carry ``rrf_score`` (higher
        is better) plus the per-retriever ranks ``keyword_rank`` and
        ``semantic_rank`` so the caller can see which path found them.
        """
        return search_tools.search_hybrid(
            db.get(),
            query=query,
            entity_types=entity_types,
            limit=limit,
        )

    @mcp.tool()
    def search_keyword(
        query: str,
        entity_types: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """FTS5 keyword search. Strict matching - use for BSI codes,
        product names, malware family names or any other exact term where
        you do NOT want semantic generalisation.

        For most questions, prefer the ``search`` tool which combines this
        with semantic retrieval. FTS5 syntax (``AND``/``OR``/``NEAR``,
        double-quoted phrases) is supported in ``query``.
        """
        return search_tools.search_keyword(
            db.get(),
            query=query,
            entity_types=entity_types,
            limit=limit,
        )

    @mcp.tool()
    def search_semantic(
        query: str,
        entity_types: list[str] | None = None,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """Pure dense vector search. Use only when you want semantic
        generalisation without keyword bias - e.g. exploring conceptually
        adjacent topics.

        For most questions, prefer the ``search`` tool. Dense retrieval
        alone tends to exhibit term-bias on rare proper nouns (it may
        cluster "Verschlüsselungstrojaner" with all encryption topics
        rather than ransomware specifically).
        """
        return search_tools.search_semantic(
            db.get(),
            query=query,
            entity_types=entity_types,
            top_k=top_k,
        )


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


def _make_health_route(db: _DbHolder):
    async def health(request: Request) -> Response:
        try:
            conn = db.get()
            row = conn.execute("SELECT COUNT(*) AS n FROM requirement").fetchone()
            return JSONResponse({"status": "ok", "requirement_count": row["n"]})
        except Exception as exc:  # noqa: BLE001
            log.exception("health_check_failed")
            return JSONResponse(
                {"status": "error", "error": str(exc)},
                status_code=503,
            )

    return Route("/health", health, methods=["GET"])


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(cfg: ServerConfig | None = None) -> Starlette:
    """Build the Starlette ASGI app. Useful for tests."""
    cfg = cfg or ServerConfig.from_env()
    _configure_logging(cfg.log_level)

    mcp = FastMCP(
        "grundschutz-mcp",
        transport_security=_build_transport_security(cfg.allowed_hosts),
    )
    # FastMCP picks up the mcp SDK's own version for serverInfo when we
    # don't override it. Report our package version instead so clients see
    # "grundschutz-mcp/0.1.0" rather than the bundled SDK release.
    try:
        mcp._mcp_server.version = _pkg_version("grundschutz-mcp")
    except PackageNotFoundError:
        # Editable installs in unusual layouts can miss the metadata; fall
        # back to a static string rather than crashing startup.
        mcp._mcp_server.version = "0.0.0+unknown"
    db_holder = _DbHolder(cfg)
    register_tools(mcp, db_holder)

    # FastMCP's streamable HTTP app already routes /mcp internally. Mount it
    # at root so the public URL stays /mcp.
    mcp_app = mcp.streamable_http_app()

    routes = [
        _make_health_route(db_holder),
        Mount("/", app=mcp_app),
    ]
    middleware = [Middleware(BearerAuthMiddleware, expected_token=cfg.auth_token)]
    # FastMCP exposes the session manager's lifespan through its router.
    # We must hoist it to the outer app so the manager's task group starts
    # before any request is served.
    inner_lifespan = mcp_app.router.lifespan_context
    return Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=inner_lifespan,
    )


def _build_transport_security(allowed_hosts: tuple[str, ...]):
    """Build the MCP-SDK ``TransportSecuritySettings`` from a host allowlist.

    The MCP Python SDK enables DNS-rebinding protection by default and
    rejects any request whose ``Host`` header is not in ``allowed_hosts``
    with HTTP 421. When the server is reached through a reverse proxy
    (Traefik in our case) the request arrives with the public domain in
    ``Host``, which must be added to the allowlist.

    Set ``allowed_hosts`` to ``("*",)`` to disable rebinding protection
    entirely - sensible behind a proxy that already enforces host routing.
    """
    from mcp.server.transport_security import TransportSecuritySettings  # heavy

    if not allowed_hosts or allowed_hosts == ("*",):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(allowed_hosts),
    )


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper())
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    import uvicorn

    cfg = ServerConfig.from_env()
    app = build_app(cfg)
    log.info(
        "server_start",
        host=cfg.host,
        port=cfg.port,
        db=str(cfg.db_path),
        auth=("on" if cfg.auth_token else "off"),
        semantic=cfg.semantic_search_enabled,
    )
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
