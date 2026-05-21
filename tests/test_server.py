"""Tests for the MCP server's HTTP layer (auth + healthcheck).

We don't drive the MCP protocol end-to-end here; the tool functions
themselves are covered in :mod:`tests.test_tools`. This file verifies
that the Starlette app is wired correctly: healthcheck returns DB stats,
the auth middleware rejects unauthenticated requests, and a valid bearer
token gets through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from grundschutz_mcp.config import ServerConfig
from grundschutz_mcp.server import build_app


@pytest.fixture
def populated_db_path(tiny_db) -> Path:
    """Return the path of the tiny_db database."""
    # PRAGMA database_list yields (seq, name, file) for each attached DB;
    # row 0 is the main DB and its third column is the on-disk path.
    row = tiny_db.execute("PRAGMA database_list").fetchone()
    return Path(row[2])


def _make_client(db_path: Path, token: str | None = None) -> TestClient:
    cfg = ServerConfig(
        db_path=db_path,
        host="127.0.0.1",
        port=0,
        auth_token=token,
        log_level="WARNING",
        semantic_search_enabled=False,
    )
    return TestClient(build_app(cfg))


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


def test_health_returns_ok_with_db_stats(populated_db_path):
    with _make_client(populated_db_path) as client:
        r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["requirement_count"] == 3  # from tiny fixture


def test_health_when_db_missing(tmp_path):
    missing = tmp_path / "absent.db"
    # Note: connect() creates the file but apply_schema isn't called, so the
    # health query against a non-existent table should fail with 503.
    with _make_client(missing) as client:
        r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "error"


# ---------------------------------------------------------------------------
# Bearer auth middleware
# ---------------------------------------------------------------------------


def test_no_auth_required_when_token_unset(populated_db_path):
    with _make_client(populated_db_path, token=None) as client:
        r = client.get("/health")
    assert r.status_code == 200


def test_auth_required_when_token_set(populated_db_path):
    with _make_client(populated_db_path, token="secret") as client:
        # /health is exempt, should still pass without token
        r = client.get("/health")
        assert r.status_code == 200
        # but /mcp/... must reject without bearer
        r = client.get("/mcp/")
        assert r.status_code == 401
        assert "bearer" in r.headers.get("www-authenticate", "").lower()


def test_auth_rejects_wrong_token(populated_db_path):
    with _make_client(populated_db_path, token="secret") as client:
        r = client.get("/mcp/", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 403


def test_auth_accepts_correct_token(populated_db_path):
    with _make_client(populated_db_path, token="secret") as client:
        # We don't expect a 200 from /mcp/ on a plain GET (MCP needs proper
        # protocol headers), but we expect to GET PAST the auth layer - i.e.
        # not a 401/403.
        r = client.get("/mcp/", headers={"Authorization": "Bearer secret"})
        assert r.status_code not in (401, 403)
