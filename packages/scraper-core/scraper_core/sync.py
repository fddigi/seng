"""Delta-sync driver: pushes only queued new/changed rows from the local SQLite
outbox to Turso. This is the enforced pattern across all projects using this
boilerplate - never a full-table rewrite, always parameter-bound per-row upserts
batched into one round trip.
"""

from __future__ import annotations

import json
import logging

from .local_db import LocalStore
from .sql_safety import safe_ident as _safe_ident
from .turso_client import TursoClient

logger = logging.getLogger(__name__)


def sync_pending(
    store: LocalStore,
    turso: TursoClient,
    *,
    conflict_column: str = "item_key",
    batch_size: int = 200,
    protected_update_columns: set[str] | None = None,
    conditional_update_columns: dict[str, str] | None = None,
) -> int:
    """Push queued delta rows to Turso and mark them synced locally.

    Each `sync_queue` row becomes one parameterized upsert statement; all pending
    rows (up to `batch_size`) are sent as a single batched round trip. Returns the
    number of rows synced.

    `protected_update_columns`/`conditional_update_columns` exist because this
    function builds its OWN generic UPDATE clause from the payload - it has no
    idea a caller's local-only upsert SQL (e.g. pipeline.py's `_INSERT_SQL`)
    deliberately protects some columns from being clobbered by a re-sync.
    Without these, a manually-curated column (dismiss flag, manually-corrected
    field) protected in the LOCAL sqlite write would still get silently
    overwritten here on the very next Turso sync triggered by an unrelated
    field changing (e.g. a price drop) - found 2026-07-23 via seng project:
    347 of 366 Turso rows had first_seen silently reset to the current run's
    timestamp this way.

    `protected_update_columns`: columns entirely OMITTED from the SET clause on
    an 'update' op (never touched by a re-sync, only ever set at initial INSERT).
    `conditional_update_columns`: column -> raw SQL expression (e.g. a CASE WHEN
    referencing another column, using `:col` for "the new value being set" -
    there is no `excluded` pseudo-table here, see below) used INSTEAD of the
    plain `:col` assignment, again only for 'update' ops.

    'insert' ops (a row's first-ever sync) use `INSERT ... ON CONFLICT DO
    NOTHING` with the FULL payload. 'update' ops (an already-synced row, e.g. a
    partial payload from `LocalStore.enqueue_update()` touching only one or two
    columns) use a PLAIN `UPDATE ... WHERE conflict_col = :conflict_col`
    instead of `INSERT ... ON CONFLICT DO UPDATE` - deliberately, NOT just a
    style choice: SQLite/libSQL validates NOT NULL constraints for EVERY column
    of the row an upsert WOULD construct, even for columns the ON CONFLICT
    branch would just update on an EXISTING row - so a partial payload used in
    an upsert form always raises "NOT NULL constraint failed" for whichever
    required column it omitted, no matter how long the row has already
    existed. Found 2026-07-23 in production: this made essentially every
    partial-payload sync fail, not just genuinely orphaned rows as first
    suspected. A plain UPDATE has no such requirement, since it never
    constructs a new row.
    """
    rows = store.pending_sync_rows(limit=batch_size)
    if not rows:
        logger.info("sync: nothing pending")
        return 0

    conflict_col = _safe_ident(conflict_column)
    protected_update_columns = protected_update_columns or set()
    conditional_update_columns = conditional_update_columns or {}
    statements: list[tuple[str, dict]] = []
    row_ids: list[int] = []

    for row in rows:
        payload: dict = json.loads(row["row_json"])
        op = row["op"]
        table = _safe_ident(row["target_table"])
        columns = [_safe_ident(c) for c in payload]
        if conflict_col not in columns:
            raise ValueError(
                f"payload for target_table={table!r} is missing conflict column "
                f"{conflict_col!r}: {list(payload.keys())}"
            )

        update_cols = [c for c in columns if c != conflict_col]

        if op == "update":
            update_cols = [c for c in update_cols if c not in protected_update_columns]
            # Only bind params actually referenced by a `:name` placeholder in
            # the generated SQL - libsql/Turso requires an EXACT match between
            # supplied named params and placeholders used, not just "extra
            # keys are ignored". A full payload still carries the
            # protected/excluded columns as dict keys even though the SQL
            # below never references them - passing the untrimmed payload
            # raised "Number of arguments mismatch" in production for every
            # row that had ANY protected column stripped from the SET clause.
            stmt_params = {conflict_col: payload[conflict_col]}
            stmt_params.update({c: payload[c] for c in update_cols})
            if not update_cols:
                # Nothing left to change (everything protected) - still must
                # mark the row synced so it doesn't retry forever.
                noop_sql = f"SELECT 1 FROM {table} WHERE {conflict_col} = :{conflict_col}"
                statements.append((noop_sql, stmt_params))
                row_ids.append(row["id"])
                continue
            set_parts = [
                f"{c} = {conditional_update_columns[c]}" if c in conditional_update_columns
                else f"{c} = :{c}"
                for c in update_cols
            ]
            sql = (
                f"UPDATE {table} SET {', '.join(set_parts)} "
                f"WHERE {conflict_col} = :{conflict_col}"
            )
        else:
            col_list = ", ".join(columns)
            placeholders = ", ".join(f":{c}" for c in columns)
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_col}) DO NOTHING"
            )
            stmt_params = payload
        statements.append((sql, stmt_params))
        row_ids.append(row["id"])

    try:
        turso.batch(statements)
    except Exception:
        # 2026-07-23: one bad statement (e.g. a minimal update-only payload
        # for an item_key Turso doesn't have yet, violating a NOT NULL
        # constraint on a column the partial payload didn't include) used to
        # fail the ENTIRE batch and, since mark_synced() never ran, left every
        # row in it - and every future pending row behind it - stuck retrying
        # the same failure forever. Found in production: a scraper run's
        # sync got wedged this way while the miss-counter above was still
        # being developed, blocking Turso sync for the whole project until
        # manually diagnosed. Falls back to one-statement-at-a-time so a
        # single poison row is isolated (logged, left unsynced, retried next
        # run) instead of blocking every OTHER pending row behind it.
        logger.exception("sync: batch failed, retrying rows individually to isolate the failure")
        synced_ids = []
        for (sql, payload), row_id in zip(statements, row_ids, strict=True):
            try:
                turso.batch([(sql, payload)])
                synced_ids.append(row_id)
            except Exception:
                logger.exception(
                    "sync: row id=%s (target_table payload keys=%s) failed, "
                    "leaving unsynced for retry: %s",
                    row_id, list(payload.keys()), payload.get("item_key"),
                )
        store.mark_synced(synced_ids)
        logger.info(
            "sync: pushed %d of %d row(s) to Turso after isolating failures",
            len(synced_ids), len(row_ids),
        )
        return len(synced_ids)

    store.mark_synced(row_ids)
    logger.info("sync: pushed %d row(s) to Turso", len(row_ids))
    return len(row_ids)
