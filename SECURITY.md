# Security policy

## Reporting a vulnerability

Please **do not** open public GitHub issues for security problems.

Instead, use GitHub's private security advisory feature:
**Security → Report a vulnerability** in this repository.
A maintainer will acknowledge within 7 days.

If GitHub's flow isn't an option, email the maintainer directly
(see `pyproject.toml` for the current contact).

## Scope

In scope:
- Code in this repository (`src/`, `schema/`, `scripts/`,
  `Dockerfile`, `compose.yml`).
- Documented configuration paths.

Out of scope:
- Vulnerabilities in upstream dependencies (report those to their
  maintainers; we will track and update).
- The BSI Kompendium content itself.
- Setups where the operator has disabled the documented guard rails
  (rate-limit, CPU cap) without compensating controls.

## Known security considerations

The server is designed for **read-only** access to public BSI content.
Even so, operators should:

1. Either set `GRUNDSCHUTZ_AUTH_TOKEN` to a strong value **or** keep
   the rate-limit middleware enabled.
2. Run behind a TLS-terminating reverse proxy (Traefik, nginx, Caddy).
3. Keep the container resource limits enabled if the host runs
   other services.
4. Update the embedding model + dependencies regularly
   (`pip install -e ".[server,embeddings]" --upgrade`).

The MCP SDK enforces DNS-rebinding protection via a `Host`-header
allowlist (CVE-2025-66416). Configure it through
`GRUNDSCHUTZ_ALLOWED_HOSTS`; the default is derived from `PUBLIC_HOST`.
