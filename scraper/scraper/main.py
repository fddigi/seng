"""Entry point for the dummy scraper.

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

from scraper.sources.jsonplaceholder import TURSO_SCHEMA, scrape_into_local_store

logger = logging.getLogger(__name__)


def run() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    try:
        with LocalStore(settings.local_sqlite_path) as store:
            changed = scrape_into_local_store(store, settings.scrape_source_url)

            if settings.turso_configured:
                with TursoClient(settings) as turso:
                    turso.execute(TURSO_SCHEMA)  # idempotent schema migration, not a data rewrite
                    synced = sync_pending(store, turso)
                logger.info("run complete: %d new/changed, %d synced to Turso", changed, synced)
            else:
                # Graceful fallback: no Turso credentials configured -> local-only mode.
                # The demo still works end-to-end without any cloud account.
                logger.warning(
                    "TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set - skipping Turso sync "
                    "(local-only demo mode). %d new/changed item(s) queued locally.",
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
