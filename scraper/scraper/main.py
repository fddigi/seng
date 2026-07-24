"""Entry point for seng-scraperen (kolonihave). Bred DBA-søgning på gemte,
allerede-filtrerede mål (config.yaml) - ingen model-genkendelse/klassifikation,
brugerens egen tilgang er kuratering af resultatet i frontend'en.

Run directly with `python -m scraper.main`, via the `scraper-run` console script, or
through the launchd job installed by `make install-launchd`.
"""

from __future__ import annotations

import logging
import sys

from scraper_core.config import get_settings
from scraper_core.healthcheck import ping_fail, ping_success
from scraper_core.local_db import LocalStore
from scraper_core.logging_setup import configure_logging
from scraper_core.sync import sync_pending
from scraper_core.turso_client import TursoClient

from scraper.beds_config import load_config
from scraper.pipeline import (
    SYNC_CONDITIONAL_COLUMNS,
    SYNC_PROTECTED_COLUMNS,
    TURSO_SCHEMA,
    run_source,
)
from scraper.schema_utils import add_column_if_missing
from scraper.sources import dba

logger = logging.getLogger(__name__)


def run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    beds_config = load_config()

    try:
        with LocalStore(settings.local_sqlite_path) as store:
            raw_count, changed = run_source(store, "dba", dba.fetch, beds_config)

            if settings.turso_configured:
                with TursoClient(settings) as turso:
                    turso.execute(TURSO_SCHEMA)  # idempotent schema migration, not a data rewrite
                    # Additive migration for the already-existing listings table
                    # (predates dismissed/dismissed_reason) - see schema_utils.py.
                    add_column_if_missing(
                        turso, "listings", "dismissed", "INTEGER NOT NULL DEFAULT 0"
                    )
                    add_column_if_missing(turso, "listings", "dismissed_reason", "TEXT")
                    add_column_if_missing(turso, "listings", "brand", "TEXT")
                    add_column_if_missing(
                        turso, "listings", "brand_manual", "INTEGER NOT NULL DEFAULT 0"
                    )
                    add_column_if_missing(turso, "listings", "image_url", "TEXT")
                    add_column_if_missing(
                        turso, "listings", "pinned", "INTEGER NOT NULL DEFAULT 0"
                    )
                    add_column_if_missing(
                        turso, "listings", "misses", "INTEGER NOT NULL DEFAULT 0"
                    )
                    # 2026-07-24: sync_pending() only drains ONE batch (200
                    # rows default) per call - a single run's own outbox
                    # volume can easily exceed that (last_seen touches for
                    # every unchanged-but-found row, PLUS misses-counter
                    # updates for every non-dismissed row not found this run,
                    # both added 2026-07-23) - found in production: the queue
                    # grew to 4328 unsynced rows over ~10 hours because each
                    # hourly run only ever cleared one batch, never catching
                    # up. Loop until fully drained (capped so a persistent
                    # failure can't spin forever - see sync_pending's own
                    # per-row isolation for why a single bad row won't block
                    # this loop either).
                    synced = 0
                    for _ in range(100):  # 100 * 200 = 20,000 rows/run ceiling
                        batch_synced = sync_pending(
                            store,
                            turso,
                            protected_update_columns=SYNC_PROTECTED_COLUMNS,
                            conditional_update_columns=SYNC_CONDITIONAL_COLUMNS,
                        )
                        synced += batch_synced
                        if batch_synced == 0:
                            break
                logger.info(
                    "run complete: %d raw, %d new/changed, %d synced to Turso",
                    raw_count, changed, synced,
                )
            else:
                # Graceful fallback: no Turso credentials configured -> local-only mode.
                logger.warning(
                    "TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set - skipping Turso sync "
                    "(local-only mode). %d new/changed item(s) queued locally.",
                    changed,
                )
    except Exception:
        logger.exception("scrape run failed")
        ping_fail(settings.healthcheck_url)
        return 1

    ping_success(settings.healthcheck_url)
    return 0


if __name__ == "__main__":
    sys.exit(run())
