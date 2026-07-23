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
    function builds its OWN generic `ON CONFLICT DO UPDATE SET col = excluded.col`
    clause from the payload - it has no idea a caller's local-only upsert SQL
    (e.g. pipeline.py's `_INSERT_SQL`) deliberately protects some columns from
    being clobbered by a re-sync. Without these, a manually-curated column
    (dismiss flag, manually-corrected field) protected in the LOCAL sqlite write
    would still get silently overwritten here on the very next Turso sync
    triggered by an unrelated field changing (e.g. a price drop) - found
    2026-07-23 via seng project: 347 of 366 Turso rows had first_seen silently
    reset to the current run's timestamp this way, because `first_seen` was
    part of every payload and got blindly set via `excluded.first_seen` on
    each re-sync.

    `protected_update_columns`: columns entirely OMITTED from the SET clause on
    an 'update' op (never touched by a re-sync, only ever set at initial INSERT).
    `conditional_update_columns`: column -> raw SQL expression (e.g. a CASE WHEN
    referencing another column) used INSTEAD of `excluded.col`, again only for
    'update' ops - 'insert' ops always use excluded.col/full values for every
    column, since there's no prior row to protect yet.
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

        col_list = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        update_cols = [c for c in columns if c != conflict_col]
        if op == "update":
            update_cols = [c for c in update_cols if c not in protected_update_columns]
        if update_cols:
            set_parts = []
            for c in update_cols:
                if op == "update" and c in conditional_update_columns:
                    set_parts.append(f"{c} = {conditional_update_columns[c]}")
                else:
                    set_parts.append(f"{c} = excluded.{c}")
            update_clause = ", ".join(set_parts)
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_col}) DO UPDATE SET {update_clause}"
            )
        else:
            sql = (
                f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_col}) DO NOTHING"
            )
        statements.append((sql, payload))
        row_ids.append(row["id"])

    turso.batch(statements)
    store.mark_synced(row_ids)
    logger.info("sync: pushed %d row(s) to Turso", len(row_ids))
    return len(row_ids)
