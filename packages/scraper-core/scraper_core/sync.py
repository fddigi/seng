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
) -> int:
    """Push queued delta rows to Turso and mark them synced locally.

    Each `sync_queue` row becomes one parameterized upsert statement; all pending
    rows (up to `batch_size`) are sent as a single batched round trip. Returns the
    number of rows synced.
    """
    rows = store.pending_sync_rows(limit=batch_size)
    if not rows:
        logger.info("sync: nothing pending")
        return 0

    conflict_col = _safe_ident(conflict_column)
    statements: list[tuple[str, dict]] = []
    row_ids: list[int] = []

    for row in rows:
        payload: dict = json.loads(row["row_json"])
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
        if update_cols:
            update_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
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
