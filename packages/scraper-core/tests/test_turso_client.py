from __future__ import annotations

from scraper_core.turso_client import _force_http_scheme


def test_force_http_scheme_rewrites_libsql_prefix():
    """Regression test: libsql-client's default WebSocket mode (derived from
    a libsql:// URL) failed to connect from a real scraper run; https://
    (HTTP transport) worked immediately against the same database."""
    assert (
        _force_http_scheme("libsql://my-db-my-org.turso.io")
        == "https://my-db-my-org.turso.io"
    )


def test_force_http_scheme_leaves_other_schemes_untouched():
    assert _force_http_scheme("https://my-db-my-org.turso.io") == "https://my-db-my-org.turso.io"
    assert _force_http_scheme("http://localhost:8080") == "http://localhost:8080"
