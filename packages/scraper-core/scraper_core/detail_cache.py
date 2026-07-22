"""OPT-IN alternative to a boolean "details fetched" flag for two-phase
(cheap list + expensive detail-lookup) scrapers.

NOT the default - see local_db.LocalStore, whose upsert_if_changed() pattern
covers most projects fine. Opt into DetailFetchCache only once your project's
detail-fetch contract has grown/changed at least once, or you expect it to;
for a stable, unchanging detail schema the simpler boolean-flag pattern has
less overhead (one column, not a JSON-encoded set) and is easier to reason
about. This class exists because a plain boolean was measurably not enough
for one real project - it is not proven to be the right default for every
project, which is why it ships separately rather than replacing
upsert_if_changed's existing behaviour.

Why it exists: a boolean "details_fetched" flag records only "have we EVER
fetched details for this item", not "which fields". PLAGG hit this twice in
the same project: adding seller_country, and later shipping_price, to the
detail-fetch contract left every already-cached row (fetched BEFORE that
field existed) permanently skipping the new lookup - the field stayed NULL in
production until someone noticed and manually reset the cache flag for
affected rows. This class tracks fetched FIELD NAMES per item instead, so a
schema change is detected automatically, per row, without a manual migration.
"""

from __future__ import annotations

import json
import sqlite3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS detail_fetch_cache (
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    fields_fetched TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (source, item_key)
);
"""


class DetailFetchCache:
    """Tracks which detail FIELDS (not just whether-fetched-at-all) have been
    retrieved for each (source, item_key) pair.

    Typical usage in a two-phase source module:

        required = {"seller_country", "shipping_price"}
        missing = cache.missing_fields(source="sellpy", item_key=key, required_fields=required)
        if missing:
            details = fetch_details(item, fields=missing)
            cache.mark_fetched(source="sellpy", item_key=key, fields=set(details))
        # else: every required field was already fetched at some point - skip
        # the expensive lookup entirely, same as the boolean-flag pattern did.
    """

    def __init__(self, connection: sqlite3.Connection):
        # Pass a LocalStore's own `.connection` to share one SQLite file/
        # transaction scope with the rest of a project's local bookkeeping,
        # e.g. DetailFetchCache(store.connection).
        self._conn = connection
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def missing_fields(
        self, *, source: str, item_key: str, required_fields: set[str]
    ) -> set[str]:
        """Returns the subset of `required_fields` NOT YET recorded as
        fetched for this item. An empty result means every required field has
        already been fetched at least once - safe to skip the expensive
        detail call entirely."""
        row = self._conn.execute(
            "SELECT fields_fetched FROM detail_fetch_cache WHERE source = ? AND item_key = ?",
            (source, item_key),
        ).fetchone()
        fetched = set(json.loads(row[0])) if row is not None else set()
        return required_fields - fetched

    def mark_fetched(self, *, source: str, item_key: str, fields: set[str]) -> None:
        """Records that `fields` have now been fetched for this item, merged
        with whatever was already recorded - never shrinks the recorded set."""
        row = self._conn.execute(
            "SELECT fields_fetched FROM detail_fetch_cache WHERE source = ? AND item_key = ?",
            (source, item_key),
        ).fetchone()
        existing = set(json.loads(row[0])) if row is not None else set()
        merged = sorted(existing | fields)
        self._conn.execute(
            """
            INSERT INTO detail_fetch_cache (source, item_key, fields_fetched)
            VALUES (?, ?, ?)
            ON CONFLICT(source, item_key) DO UPDATE SET fields_fetched = excluded.fields_fetched
            """,
            (source, item_key, json.dumps(merged)),
        )
        self._conn.commit()
