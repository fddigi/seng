from __future__ import annotations

import sqlite3

from scraper_core.generations import (
    cleanup_superseded,
    ensure_control_table,
    publish_generation,
)


class FakeTursoClient:
    """In-memory SQLite-backed stand-in for TursoClient. Unlike a pure
    call-recording fake, this actually executes the SQL - important here
    because the thing under test IS the SQL itself (the "only if newer"
    ON CONFLICT...WHERE guard), not just what statements get sent."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur.fetchall()

    def batch(self, statements):
        results = []
        for sql, params in statements:
            cur = self._conn.execute(sql, params)
            results.append(cur.fetchall())
        self._conn.commit()
        return results

    def rows(self, table: str) -> list[sqlite3.Row]:
        return self._conn.execute(f"SELECT * FROM {table} ORDER BY item_key").fetchall()

    def close(self):
        self._conn.close()


def _make_data_table(turso: FakeTursoClient) -> None:
    turso.execute(
        "CREATE TABLE matches ("
        "item_key TEXT PRIMARY KEY, title TEXT NOT NULL, run_id INTEGER NOT NULL"
        ")"
    )


def test_publish_generation_writes_rows_and_sets_current_run_id():
    turso = FakeTursoClient()
    _make_data_table(turso)
    ensure_control_table(turso, "generation_control")

    publish_generation(
        turso,
        control_table="generation_control",
        control_key="matches",
        data_table="matches",
        run_id=100,
        rows=[{"item_key": "a", "title": "Match A"}],
    )

    rows = turso.rows("matches")
    assert len(rows) == 1
    assert rows[0]["run_id"] == 100
    control = turso.execute(
        "SELECT current_run_id FROM generation_control WHERE key = ?", ("matches",)
    )
    assert control[0]["current_run_id"] == 100
    turso.close()


def test_publish_generation_regression_older_run_never_clobbers_newer():
    """Regression test: PLAGG's real race condition - an older, slower run
    finishing AFTER a newer run must never overwrite the newer result."""
    turso = FakeTursoClient()
    _make_data_table(turso)
    ensure_control_table(turso, "generation_control")

    # Newer run publishes first (e.g. it started later but ran faster).
    publish_generation(
        turso,
        control_table="generation_control",
        control_key="matches",
        data_table="matches",
        run_id=200,
        rows=[{"item_key": "b", "title": "Match B (newer)"}],
    )
    # Older, slow-to-finish run publishes second - must NOT win.
    publish_generation(
        turso,
        control_table="generation_control",
        control_key="matches",
        data_table="matches",
        run_id=100,
        rows=[{"item_key": "a", "title": "Match A (older, stale)"}],
    )

    control = turso.execute(
        "SELECT current_run_id FROM generation_control WHERE key = ?", ("matches",)
    )
    assert control[0]["current_run_id"] == 200  # still the newer one, unchanged
    turso.close()


def test_publish_generation_empty_batch_is_a_valid_publish():
    """A run that finds zero matches is a real result ("nothing matched this
    time"), not a no-op - the control table must still advance."""
    turso = FakeTursoClient()
    _make_data_table(turso)
    ensure_control_table(turso, "generation_control")

    publish_generation(
        turso,
        control_table="generation_control",
        control_key="matches",
        data_table="matches",
        run_id=100,
        rows=[],
    )

    control = turso.execute(
        "SELECT current_run_id FROM generation_control WHERE key = ?", ("matches",)
    )
    assert control[0]["current_run_id"] == 100
    assert turso.rows("matches") == []
    turso.close()


def test_cleanup_superseded_removes_only_old_generation_rows():
    turso = FakeTursoClient()
    _make_data_table(turso)
    ensure_control_table(turso, "generation_control")

    publish_generation(
        turso,
        control_table="generation_control",
        control_key="matches",
        data_table="matches",
        run_id=100,
        rows=[{"item_key": "a", "title": "old"}],
    )
    publish_generation(
        turso,
        control_table="generation_control",
        control_key="matches",
        data_table="matches",
        run_id=200,
        rows=[{"item_key": "b", "title": "new"}],
    )
    # Both generations' rows physically coexist until cleanup runs.
    assert len(turso.rows("matches")) == 2

    cleanup_superseded(
        turso, control_table="generation_control", control_key="matches", data_table="matches"
    )

    remaining = turso.rows("matches")
    assert len(remaining) == 1
    assert remaining[0]["item_key"] == "b"
    turso.close()


def test_ensure_control_table_is_idempotent():
    turso = FakeTursoClient()
    ensure_control_table(turso, "generation_control")
    ensure_control_table(turso, "generation_control")  # must not raise
    turso.close()
