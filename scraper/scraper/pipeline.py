"""Adapter layer connecting dba.py's fetch() to scraper-core's delta-sync pattern
(LocalStore.upsert_if_changed + the Turso outbox).

`last_seen` er indbygget fra start (lært af PA SPEAKERS-projektet, hvor den blev
tilføjet efterfølgende): hver annonce der stadig dukker op i en scrape-køring får
last_seen opdateret, uanset om noget ved den ellers har ændret sig. En annonce
der IKKE er genberørt i lang tid er sandsynligvis solgt/nedtaget - frontend'en
bruger dette til at skjule den, uden at prishistorik-data nogensinde slettes.

`dismissed`/`dismissed_reason`: auto-afvisning af lav-kvalitets-mærker (IKEA/JYSK,
se config.yaml:auto_dismiss_brands) SAMT manuel afvisning fra frontend'en (via
worker'ens /api/listings/:itemKey/dismiss, som skriver direkte til Turso UDENOM
scraperen). Begge dele sættes KUN ved en annonces FØRSTE indsættelse her - en
senere re-sync (fx et prisfald) rører ALDRIG disse to kolonner (se ON CONFLICT-
klausulen), så en brugers manuelle afvisning aldrig overskrives af scraperen.
"""
from __future__ import annotations

import datetime
import hashlib
import logging

from scraper_core.local_db import LocalStore
from scraper_core.watchdog import SourceTimeoutError, run_with_timeout

from .schema_utils import add_column_if_missing

logger = logging.getLogger(__name__)

TARGET_TABLE = "listings"

LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    item_key TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    title TEXT NOT NULL,
    price_dkk REAL,
    url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    dismissed_reason TEXT
);
"""
TURSO_SCHEMA = LOCAL_SCHEMA

_INSERT_SQL = """
INSERT INTO listings (item_key, target, title, price_dkk, url, first_seen, last_seen,
    dismissed, dismissed_reason)
VALUES (:item_key, :target, :title, :price_dkk, :url, :first_seen, :last_seen,
    :dismissed, :dismissed_reason)
ON CONFLICT(item_key) DO UPDATE SET
    target = excluded.target, title = excluded.title, price_dkk = excluded.price_dkk,
    url = excluded.url, last_seen = excluded.last_seen
    -- dismissed/dismissed_reason er BEVIDST udeladt her, se modulets docstring.
"""


def make_item_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _auto_dismiss(title: str, target_name: str, config: dict) -> tuple[bool, str | None]:
    """Returnerer (dismissed, reason) baseret på config.yaml:auto_dismiss_brands -
    gælder ikke mål der selv har skip_auto_dismiss: true (se config.yaml:
    "Valevåg (IKEA)", som bevidst SØGER efter en IKEA-model)."""
    targets_by_name = {t["name"]: t for t in config.get("targets", [])}
    if targets_by_name.get(target_name, {}).get("skip_auto_dismiss"):
        return False, None

    title_lower = title.lower()
    for brand in config.get("auto_dismiss_brands", []):
        if brand.lower() in title_lower:
            return True, f"auto:{brand.lower()}"
    return False, None


def run_source(
    store: LocalStore,
    source_name: str,
    fetch_fn,
    config: dict,
    dry_run: bool = False,
    fetch_timeout_seconds: float = 600,
) -> tuple[int, int]:
    """Runs fetch() -> dedup -> upsert_if_changed, isolated try/except (one
    source's failure never crashes the run). Returns (raw_count, changed_count)."""
    store.executescript(LOCAL_SCHEMA)
    add_column_if_missing(store.connection, "listings", "dismissed", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(store.connection, "listings", "dismissed_reason", "TEXT")
    raw_count = 0
    changed = 0

    try:
        raw_listings = run_with_timeout(
            lambda: fetch_fn(config, dry_run=dry_run),
            timeout_seconds=fetch_timeout_seconds,
            source_name=source_name,
        )
        raw_count = len(raw_listings)
        now = datetime.datetime.now(datetime.UTC).isoformat()

        for raw in raw_listings:
            url = raw.get("url", "")
            if not url:
                continue
            item_key = make_item_key(url)
            title = raw.get("title", "")
            target_name = raw.get("extra", {}).get("target", "?")
            dismissed, dismissed_reason = _auto_dismiss(title, target_name, config)

            payload = {
                "item_key": item_key,
                "target": target_name,
                "title": title,
                "price_dkk": raw.get("price_amount"),
                "url": url,
                "first_seen": now,
                "last_seen": now,
                "dismissed": int(dismissed),
                "dismissed_reason": dismissed_reason,
            }

            is_new_or_changed = store.upsert_if_changed(
                source=source_name,
                item_key=item_key,
                payload=payload,
                target_table=TARGET_TABLE,
                # first_seen/last_seen udelukkes: saettes til "nu" hver koersel.
                # dismissed/dismissed_reason udelukkes OGSAA: en manuel afvisning
                # sker direkte i Turso (uden om scraperens lokale sqlite), saa
                # disse to felter maa aldrig indgaa i "har annoncen aendret
                # sig?"-sammenligningen - se modulets docstring.
                hash_payload={
                    k: v for k, v in payload.items()
                    if k not in ("first_seen", "last_seen", "dismissed", "dismissed_reason")
                },
            )
            if not is_new_or_changed:
                # Uaendret, men STADIG FUNDET i denne koersel - det er selve
                # signalet "sandsynligvis stadig til salg". Uden denne touch
                # ville en uaendret annonce aldrig faa last_seen opdateret
                # efter foerste indsaettelse.
                store.connection.execute(
                    "UPDATE listings SET last_seen = ? WHERE item_key = ?",
                    (now, item_key),
                )
                store.connection.commit()
                continue

            store.connection.execute(_INSERT_SQL, payload)
            store.connection.commit()
            changed += 1

        logger.info("%s: %d raw, %d new/changed", source_name, raw_count, changed)
    except SourceTimeoutError:
        pass
    except Exception:
        logger.exception("%s: source failed, skipping - other sources unaffected", source_name)

    return raw_count, changed
