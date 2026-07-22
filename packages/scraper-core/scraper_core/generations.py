"""Generation-swap pattern: atomic "replace the whole batch, or not at all"
publishing to Turso - a second, equally first-class alternative to
local_db.LocalStore/sync.sync_pending's row-level delta-sync.

Built after a real production pattern (PLAGG, found while retrofitting this
package onto an already-live project): delta-sync (only send new/changed
rows) is the right default for most scraper output, but doesn't fit every
shape. PLAGG's matches/bundles tables are fully RECOMPUTED on every run (a
fresh match of the whole wishlist against all current listings) - there is
no sensible row-level "diff" against the previous run. Two overlapping
scraper runs writing partial results directly into such a table caused a
real race condition (an already-fixed PLAGG bug): a reader could see a mix
of rows from two different runs at once.

The fix generalized here: allocate a fresh run_id, write the ENTIRE new
batch tagged with that run_id (never touching existing rows), then
atomically flip a single "current_run_id" pointer - but ONLY if the new
run_id is actually newer than what's published, so an older/slower run that
happens to finish after a newer one can never clobber it. Readers always see
either the complete previous generation or the complete new one, never a
mix. cleanup_superseded() then removes rows left behind by old generations.

Use delta-sync for "did this specific item change". Use this for "here is
this run's complete, all-or-nothing result set".
"""

from __future__ import annotations

import time
from typing import Any

from .sql_safety import safe_ident
from .turso_client import TursoClient


def allocate_run_id() -> int:
    """A monotonically increasing run id (epoch milliseconds). Not a
    database identity column - callers write this value into every row of a
    batch and into the control table, so it must be unique and increasing
    across processes without a round trip to allocate it."""
    return int(time.time() * 1000)


def ensure_control_table(turso: TursoClient, control_table: str) -> None:
    """Creates the generation-control table if it doesn't already exist.
    Safe/idempotent to call on every run."""
    table = safe_ident(control_table)
    turso.execute(
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "key TEXT PRIMARY KEY, "
        "current_run_id INTEGER NOT NULL"
        ")"
    )


def publish_generation(
    turso: TursoClient,
    *,
    control_table: str,
    control_key: str,
    data_table: str,
    run_id: int,
    rows: list[dict[str, Any]],
    run_id_column: str = "run_id",
) -> None:
    """Writes `rows` (each tagged with `run_id`) into `data_table`, then
    atomically publishes this generation by updating `control_table` - but
    ONLY if `run_id` is newer than whatever is already published there.

    `control_table` must already exist (see ensure_control_table()).
    `data_table` is NOT created here - that's project-specific, created via
    your own migration - but it must have a column named `run_id_column`.

    All rows + the control-table upsert are sent as a single Turso batch
    (one round trip), so a crash mid-write never leaves a partially-written
    generation visible to readers: either the whole batch lands, or none of
    it does, and even if it lands, the control-table pointer only updates
    if it's actually newer.
    """
    data = safe_ident(data_table)
    control = safe_ident(control_table)
    run_col = safe_ident(run_id_column)

    statements: list[tuple[str, dict]] = []
    for row in rows:
        payload = {**row, run_col: run_id}
        columns = [safe_ident(c) for c in payload]
        col_list = ", ".join(columns)
        placeholders = ", ".join(f":{c}" for c in columns)
        statements.append((f"INSERT INTO {data} ({col_list}) VALUES ({placeholders})", payload))

    # "Only if newer" guard: this upsert only actually changes current_run_id
    # if the new value is greater than what's already there - an overlapping
    # older run finishing later can never clobber a newer run's published
    # result. If rows is empty, this still runs, so an all-empty generation
    # (e.g. "no matches this run") is a valid, real publish, not a no-op.
    statements.append(
        (
            f"INSERT INTO {control} (key, current_run_id) VALUES (:key, :run_id) "
            f"ON CONFLICT(key) DO UPDATE SET current_run_id = :run_id "
            f"WHERE :run_id > {control}.current_run_id",
            {"key": control_key, "run_id": run_id},
        )
    )
    turso.batch(statements)


def cleanup_superseded(
    turso: TursoClient,
    *,
    control_table: str,
    control_key: str,
    data_table: str,
    run_id_column: str = "run_id",
) -> None:
    """Deletes rows from `data_table` belonging to any generation OTHER than
    the currently-published one. Safe to call any time after
    publish_generation() - it only ever deletes rows tagged with an older
    run_id than what's currently published, never the live generation."""
    data = safe_ident(data_table)
    control = safe_ident(control_table)
    run_col = safe_ident(run_id_column)
    turso.execute(
        f"DELETE FROM {data} WHERE {run_col} != "
        f"(SELECT current_run_id FROM {control} WHERE key = ?)",
        (control_key,),
    )
