from __future__ import annotations

import pytest

from scraper_core.local_db import LocalStore
from scraper_core.sync import sync_pending


class FakeTursoClient:
    """Stand-in for TursoClient - records batches without touching the network,
    so delta-sync logic can be unit tested without a real Turso account."""

    def __init__(self):
        self.batches: list[list[tuple[str, dict]]] = []

    def batch(self, statements):
        self.batches.append(list(statements))
        return [None] * len(statements)


@pytest.fixture
def store(tmp_path):
    s = LocalStore(tmp_path / "local.db")
    yield s
    s.close()


def test_sync_pending_pushes_only_queued_rows(store):
    store.upsert_if_changed(
        source="dummy", item_key="1", payload={"item_key": "1", "title": "a"}, target_table="posts"
    )
    store.upsert_if_changed(
        source="dummy", item_key="2", payload={"item_key": "2", "title": "b"}, target_table="posts"
    )
    fake_turso = FakeTursoClient()

    synced_count = sync_pending(store, fake_turso)

    assert synced_count == 2
    assert len(fake_turso.batches) == 1
    assert len(fake_turso.batches[0]) == 2
    assert store.pending_sync_rows() == []  # everything marked synced, nothing left in outbox


def test_sync_pending_is_a_noop_when_queue_empty(store):
    fake_turso = FakeTursoClient()
    assert sync_pending(store, fake_turso) == 0
    assert fake_turso.batches == []


def test_sync_pending_generates_parameterized_upsert_sql(store):
    store.upsert_if_changed(
        source="dummy", item_key="1", payload={"item_key": "1", "title": "a"}, target_table="posts"
    )
    fake_turso = FakeTursoClient()
    sync_pending(store, fake_turso)

    sql, params = fake_turso.batches[0][0]
    assert "INSERT INTO posts" in sql
    assert "ON CONFLICT(item_key)" in sql
    assert params == {"item_key": "1", "title": "a"}
    # No raw values are interpolated into the SQL text itself:
    assert "'a'" not in sql
    assert '"a"' not in sql


def test_sync_pending_rejects_unsafe_table_name(store):
    # Force an unsafe target_table directly via the outbox to prove sync_pending
    # allow-lists identifiers rather than trusting stored data blindly.
    store.connection.execute(
        "INSERT INTO sync_queue (target_table, op, row_json) VALUES (?, ?, ?)",
        ("posts; DROP TABLE users;--", "insert", '{"item_key": "1"}'),
    )
    store.connection.commit()
    fake_turso = FakeTursoClient()

    with pytest.raises(ValueError):
        sync_pending(store, fake_turso)


def test_sync_pending_requires_conflict_column_in_payload(store):
    store.connection.execute(
        "INSERT INTO sync_queue (target_table, op, row_json) VALUES (?, ?, ?)",
        ("posts", "insert", '{"title": "no key field"}'),
    )
    store.connection.commit()
    fake_turso = FakeTursoClient()

    with pytest.raises(ValueError):
        sync_pending(store, fake_turso)


def test_sync_pending_respects_batch_size(store):
    for i in range(5):
        store.upsert_if_changed(
            source="dummy",
            item_key=str(i),
            payload={"item_key": str(i), "v": i},
            target_table="posts",
        )
    fake_turso = FakeTursoClient()

    synced_count = sync_pending(store, fake_turso, batch_size=2)

    assert synced_count == 2
    assert len(store.pending_sync_rows(limit=100)) == 3
