"""Thin wrapper around the official `libsql-client` Python SDK.

No hand-rolled HTTP against Turso's /v2/pipeline endpoint - the SDK handles transport.
Always parameter binding, never string interpolation into SQL text.
"""

from __future__ import annotations

from typing import Any

import libsql_client

from .config import Settings


def _force_http_scheme(url: str) -> str:
    """Rewrites a `libsql://` URL to `https://`, forcing libsql-client into
    HTTP transport instead of its default WebSocket-based Hrana protocol.

    Found the hard way: `create_client_sync()`'s default WebSocket mode
    (`wss://`, derived from a `libsql://` URL) failed outright from a real
    launchd-scheduled scraper run against a real Turso database
    (`WSServerHandshakeError: 400`), while the exact same URL with `https://`
    connected and executed immediately - WebSocket handshakes are more
    sensitive to firewalls/proxies/network environments than plain HTTPS.
    A short-lived, periodic script like this one gets none of Hrana's
    connection-reuse/streaming benefits anyway, so HTTP mode is strictly
    the better default here - not just a workaround for one flaky network.
    """
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://") :]
    return url


class TursoClient:
    """Sync Turso/libSQL client for use from simple launchd-triggered scripts."""

    def __init__(self, settings: Settings):
        if not settings.turso_configured:
            raise RuntimeError(
                "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN not set - cannot create a Turso client. "
                "Callers should check `settings.turso_configured` first and fall back to "
                "local-only mode instead of constructing this class."
            )
        self._client = libsql_client.create_client_sync(
            url=_force_http_scheme(settings.turso_database_url),
            auth_token=settings.turso_auth_token,
        )

    def execute(self, sql: str, params: tuple | list | dict = ()) -> libsql_client.ResultSet:
        """Execute a single parameterized statement."""
        return self._client.execute(sql, params)

    def batch(self, statements: list[tuple[str, Any]]) -> list[libsql_client.ResultSet]:
        """Execute several parameterized statements as one round trip / transaction.

        `statements` is a list of (sql, params) tuples - always parameter-bound.
        """
        stmts = [libsql_client.Statement(sql, params) for sql, params in statements]
        return self._client.batch(stmts)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TursoClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
