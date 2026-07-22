from __future__ import annotations

from scraper_core.local_db import LocalStore, content_hash


def test_content_hash_stable_regardless_of_key_order():
    a = {"id": 1, "title": "hello"}
    b = {"title": "hello", "id": 1}
    assert content_hash(a) == content_hash(b)


def test_content_hash_changes_on_value_change():
    a = {"id": 1, "title": "hello"}
    b = {"id": 1, "title": "goodbye"}
    assert content_hash(a) != content_hash(b)


def test_upsert_if_changed_new_item_is_queued(tmp_path):
    store = LocalStore(tmp_path / "local.db")
    changed = store.upsert_if_changed(
        source="dummy", item_key="1", payload={"item_key": "1", "title": "a"}, target_table="posts"
    )
    assert changed is True

    pending = store.pending_sync_rows()
    assert len(pending) == 1
    assert pending[0]["op"] == "insert"
    assert pending[0]["target_table"] == "posts"
    store.close()


def test_upsert_if_changed_unchanged_item_not_requeued(tmp_path):
    store = LocalStore(tmp_path / "local.db")
    payload = {"item_key": "1", "title": "a"}
    store.upsert_if_changed(source="dummy", item_key="1", payload=payload, target_table="posts")
    store.mark_synced([r["id"] for r in store.pending_sync_rows()])

    changed = store.upsert_if_changed(
        source="dummy", item_key="1", payload=payload, target_table="posts"
    )
    assert changed is False
    assert store.pending_sync_rows() == []
    store.close()


def test_upsert_if_changed_modified_item_is_requeued_as_update(tmp_path):
    store = LocalStore(tmp_path / "local.db")
    store.upsert_if_changed(
        source="dummy", item_key="1", payload={"item_key": "1", "title": "a"}, target_table="posts"
    )
    store.mark_synced([r["id"] for r in store.pending_sync_rows()])

    changed = store.upsert_if_changed(
        source="dummy", item_key="1", payload={"item_key": "1", "title": "b"}, target_table="posts"
    )
    assert changed is True
    pending = store.pending_sync_rows()
    assert len(pending) == 1
    assert pending[0]["op"] == "update"
    store.close()


def test_upsert_if_changed_hash_payload_ignores_volatile_fields(tmp_path):
    """Regression test: a field like scraped_at that changes every run must not
    defeat dedup when it's excluded from hash_payload."""
    store = LocalStore(tmp_path / "local.db")
    store.upsert_if_changed(
        source="dummy",
        item_key="1",
        payload={"item_key": "1", "title": "a", "scraped_at": "2026-01-01T00:00:00Z"},
        target_table="posts",
        hash_payload={"item_key": "1", "title": "a"},
    )
    store.mark_synced([r["id"] for r in store.pending_sync_rows()])

    changed = store.upsert_if_changed(
        source="dummy",
        item_key="1",
        payload={"item_key": "1", "title": "a", "scraped_at": "2026-01-02T00:00:00Z"},
        target_table="posts",
        hash_payload={"item_key": "1", "title": "a"},
    )
    assert changed is False
    assert store.pending_sync_rows() == []
    store.close()


def test_mark_synced_only_affects_given_ids(tmp_path):
    store = LocalStore(tmp_path / "local.db")
    store.upsert_if_changed(
        source="dummy", item_key="1", payload={"item_key": "1", "v": 1}, target_table="t"
    )
    store.upsert_if_changed(
        source="dummy", item_key="2", payload={"item_key": "2", "v": 1}, target_table="t"
    )
    ids = [r["id"] for r in store.pending_sync_rows()]
    store.mark_synced(ids[:1])

    remaining = store.pending_sync_rows()
    assert len(remaining) == 1
    assert remaining[0]["id"] == ids[1]
    store.close()
