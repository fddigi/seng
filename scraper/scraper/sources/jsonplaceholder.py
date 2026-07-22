"""Dummy example source: JSONPlaceholder (https://jsonplaceholder.typicode.com), a free,
stable public test API. This is a stand-in for a real per-project scraper source and
demonstrates the full chain end-to-end: fetch -> local dedup -> local SQLite -> Turso
delta sync.

Replace this file (or add siblings) with your project's real scraping logic. Keep the
same shape: a `LOCAL_SCHEMA`/`TURSO_SCHEMA` pair and a `scrape_into_local_store()`
function that only ever queues new/changed rows via `LocalStore.upsert_if_changed`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import requests
from scraper_core.local_db import LocalStore

logger = logging.getLogger(__name__)

SOURCE_NAME = "jsonplaceholder_posts"
TARGET_TABLE = "posts"

# Kept as two names (LOCAL_SCHEMA / TURSO_SCHEMA) on purpose, even though they start
# out identical: local SQLite and the Turso mirror are allowed to diverge over time
# (e.g. local-only debug columns) without that implying a shared migration file.
LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    item_key TEXT PRIMARY KEY,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    scraped_at TEXT NOT NULL
);
"""
TURSO_SCHEMA = LOCAL_SCHEMA


def fetch(source_url: str) -> list[dict]:
    resp = requests.get(source_url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def scrape_into_local_store(store: LocalStore, source_url: str) -> int:
    """Fetch remote items, dedup against local 'seen' state, write only new/changed
    rows into the local `posts` table and queue them for Turso sync.

    Returns the number of new/changed items found this run.
    """
    store.executescript(LOCAL_SCHEMA)
    items = fetch(source_url)
    scraped_at = datetime.now(UTC).isoformat()

    changed_count = 0
    for item in items:
        item_key = str(item["id"])
        payload = {
            "item_key": item_key,
            "post_id": item["id"],
            "user_id": item["userId"],
            "title": item["title"],
            "body": item["body"],
            "scraped_at": scraped_at,
        }
        is_new_or_changed = store.upsert_if_changed(
            source=SOURCE_NAME,
            item_key=item_key,
            payload=payload,
            target_table=TARGET_TABLE,
            # Exclude scraped_at from change detection: it's a fresh timestamp every
            # run and would otherwise defeat dedup entirely.
            hash_payload={k: v for k, v in payload.items() if k != "scraped_at"},
        )
        if not is_new_or_changed:
            continue

        store.connection.execute(
            """
            INSERT INTO posts (item_key, post_id, user_id, title, body, scraped_at)
            VALUES (:item_key, :post_id, :user_id, :title, :body, :scraped_at)
            ON CONFLICT(item_key) DO UPDATE SET
                post_id = excluded.post_id,
                user_id = excluded.user_id,
                title = excluded.title,
                body = excluded.body,
                scraped_at = excluded.scraped_at
            """,
            payload,
        )
        store.connection.commit()
        changed_count += 1

    logger.info("scrape: fetched %d item(s), %d new/changed", len(items), changed_count)
    return changed_count
