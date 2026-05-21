"""Server configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# DNS-rebinding allowlist defaults: covers in-container healthcheck and
# typical local development. Add the public domain via env var (or rely
# on the empty default => protection disabled, which is what we want
# when Traefik already enforces host routing).
_DEFAULT_ALLOWED_HOSTS = ("localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*")


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path
    host: str
    port: int
    auth_token: str | None
    log_level: str
    semantic_search_enabled: bool
    # MCP DNS-rebinding-protection allowlist. Comma-separated string from
    # GRUNDSCHUTZ_ALLOWED_HOSTS. Empty list disables the protection (we
    # rely on Traefik's Host routing for the same guarantee).
    allowed_hosts: tuple[str, ...] = field(default_factory=lambda: _DEFAULT_ALLOWED_HOSTS)

    @classmethod
    def from_env(cls) -> ServerConfig:
        raw_hosts = os.environ.get("GRUNDSCHUTZ_ALLOWED_HOSTS", "")
        if raw_hosts.strip():
            allowed = tuple(h.strip() for h in raw_hosts.split(",") if h.strip())
        else:
            # Empty env -> use the local defaults so the healthcheck works.
            # If you want zero protection (because of an upstream proxy),
            # set GRUNDSCHUTZ_ALLOWED_HOSTS="*".
            allowed = _DEFAULT_ALLOWED_HOSTS
        return cls(
            db_path=Path(os.environ.get("GRUNDSCHUTZ_DB", "db/grundschutz.db")),
            host=os.environ.get("GRUNDSCHUTZ_HOST", "127.0.0.1"),
            port=int(os.environ.get("GRUNDSCHUTZ_PORT", "8080")),
            auth_token=os.environ.get("GRUNDSCHUTZ_AUTH_TOKEN") or None,
            log_level=os.environ.get("GRUNDSCHUTZ_LOG_LEVEL", "WARNING"),
            semantic_search_enabled=(
                os.environ.get("GRUNDSCHUTZ_SEMANTIC", "1") not in ("0", "false", "False")
            ),
            allowed_hosts=allowed,
        )
