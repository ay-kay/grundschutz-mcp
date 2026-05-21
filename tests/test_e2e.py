"""End-to-end test: spin up the MCP server against the real DB and call a
handful of tools through the MCP protocol over HTTP.

Skipped when the prebuilt database (or the source data to build it) is not
available.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "grundschutz.db"

pytestmark = pytest.mark.skipif(
    not DB_PATH.is_file(),
    reason=(
        "db/grundschutz.db not built. Run `make build` (or "
        "`python -m grundschutz_mcp.build all`) first."
    ),
)


@pytest.fixture(scope="module")
def server_url():
    """Start the server in a background thread and yield its base URL."""
    import socket
    import threading
    import time

    import uvicorn

    from grundschutz_mcp.config import ServerConfig
    from grundschutz_mcp.server import build_app

    # Find a free port.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    cfg = ServerConfig(
        db_path=DB_PATH,
        host="127.0.0.1",
        port=port,
        auth_token=None,
        log_level="WARNING",
        semantic_search_enabled=False,
    )
    app = build_app(cfg)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for startup.
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)
    else:
        pytest.fail("server did not start in time")

    base = f"http://127.0.0.1:{port}"
    yield base

    server.should_exit = True
    thread.join(timeout=5)


def _call_tool(base_url: str, tool: str, arguments: dict) -> dict:
    """Speak the MCP streamable-http protocol just enough to call one tool."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go() -> dict:
        async with streamable_http_client(f"{base_url}/mcp/") as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
                # ToolResult.content is a list of TextContent etc.
                payload: dict = {"content": []}
                if result.content:
                    payload["content"] = [
                        c.text if hasattr(c, "text") else str(c) for c in result.content
                    ]
                payload["isError"] = bool(getattr(result, "isError", False))
                return payload

    return asyncio.run(go())


# ---------------------------------------------------------------------------


def test_healthcheck_reports_full_requirement_count(server_url):
    import httpx

    r = httpx.get(f"{server_url}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # 2125 with our local patches, 2080 with upstream's current 2023 JSON
    # (which is missing APP.4.6 and SYS.3.2.2 - patches pending upstream).
    assert body["requirement_count"] in (2080, 2125)


def test_list_layers_over_mcp(server_url):
    result = _call_tool(server_url, "list_layers", {})
    assert not result["isError"]
    assert any("APP" in c for c in result["content"])
    assert any("ISMS" in c for c in result["content"])


def test_get_requirement_over_mcp(server_url):
    result = _call_tool(server_url, "get_requirement", {"code": "APP.1.1.A2"})
    assert not result["isError"]
    body = "\n".join(result["content"])
    assert "Einschränken von Aktiven Inhalten" in body
    assert "Basis" in body


def test_search_keyword_over_mcp(server_url):
    result = _call_tool(
        server_url,
        "search_keyword",
        {"query": "Datensicherung", "entity_types": ["module"], "limit": 5},
    )
    assert not result["isError"]
    body = "\n".join(result["content"])
    assert "CON.3" in body


def test_get_threats_for_requirement_over_mcp(server_url):
    result = _call_tool(
        server_url,
        "get_threats_for_requirement",
        {"req_code": "APP.1.1.A2"},
    )
    assert not result["isError"]


def test_unknown_code_returns_suggestions_over_mcp(server_url):
    result = _call_tool(
        server_url,
        "get_requirement",
        {"code": "APP.1.1.X9"},
    )
    body = "\n".join(result["content"])
    assert "not_found" in body
    # Closest match should be among APP.1.1.A* requirements.
    assert "APP.1.1.A" in body
