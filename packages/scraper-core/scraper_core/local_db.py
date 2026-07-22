"""Local SQLite handling: dedup ('seen') bookkeeping + an outbox queue for delta-sync.

Enforced pattern: dedup/"seen" logic lives ONLY here, locally. Only new/changed rows
are ever queued for Turso sync - never full-table rewrites.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_items (
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, item_key)
);

CREATE TABLE IF NOT EXISTS sync_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_table TEXT NOT NULL,
    op TEXT NOT NULL CHECK (op IN ('insert', 'update')),
    row_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    synced_at TEXT
);
"""


def content_hash(payload: dict) -> str:
    """Stable hash of a row's content, used to detect new vs. changed vs. unchanged."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LocalStore:
    """Wraps a local SQLite file with dedup bookkeeping and a delta-sync outbox."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> LocalStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def executescript(self, sql: str) -> None:
        """Lets individual scrapers add their own project-specific local tables."""
        self._conn.executescript(sql)
        self._conn.commit()

    def upsert_if_changed(
        self,
        *,
        source: str,
        item_key: str,
        payload: dict,
        target_table: str,
        hash_payload: dict | None = None,
    ) -> bool:
        """Dedup-check via content hash.

        If the item is new or its content changed, records it as 'seen' and enqueues
        exactly one delta-sync row (never a full-table rewrite). Returns True if the
        item was new/changed, False if it was an unchanged repeat.

        `hash_payload` lets callers exclude volatile fields from the change-detection
        hash while still writing them as part of the synced `payload`. Defaults to
        `payload` itself. Rule of thumb: exclude any field that reflects COLLECTION
        metadata (when it was scraped, how, which search found it - e.g. a
        `scraped_at` timestamp, or a `search_term`/`matched_query` field that can
        legitimately differ between runs for the same unchanged item) rather than
        the scraped CONTENT itself. Forgetting this causes spurious re-queues for
        items that didn't actually change - not just for timestamps.
        """
        new_hash = content_hash(hash_payload if hash_payload is not None else payload)
        row = self._conn.execute(
            "SELECT content_hash FROM seen_items WHERE source = ? AND item_key = ?",
            (source, item_key),
        ).fetchone()

        if row is not None and row["content_hash"] == new_hash:
            self._conn.execute(
                "UPDATE seen_items SET last_seen_at = datetime('now') "
                "WHERE source = ? AND item_key = ?",
                (source, item_key),
            )
            self._conn.commit()
            return False

        op = "update" if row is not None else "insert"
        self._conn.execute(
            """
            INSERT INTO seen_items (source, item_key, content_hash)
            VALUES (?, ?, ?)
            ON CONFLICT(source, item_key) DO UPDATE SET
                content_hash = excluded.content_hash,
                last_seen_at = datetime('now')
            """,
            (source, item_key, new_hash),
        )
        self._conn.execute(
            "INSERT INTO sync_queue (target_table, op, row_json) VALUES (?, ?, ?)",
            (target_table, op, json.dumps(payload, default=str)),
        )
        self._conn.commit()
        return True

    def pending_sync_rows(self, limit: int = 500) -> list[sqlite3.Row]:
        """Rows not yet pushed to Turso, oldest first."""
        return self._conn.execute(
            "SELECT * FROM sync_queue WHERE synced_at IS NULL ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()

    def mark_synced(self, ids: Iterable[int]) -> None:
        """Mark outbox rows as synced after a successful Turso write."""
        ids = list(ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)  # ids are our own ints, not external input
        self._conn.execute(
            f"UPDATE sync_queue SET synced_at = datetime('now') WHERE id IN ({placeholders})",
            ids,
        )
        self._conn.commit()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn
