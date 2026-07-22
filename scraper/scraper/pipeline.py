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
import re

from scraper_core.local_db import LocalStore
from scraper_core.watchdog import SourceTimeoutError, run_with_timeout

from .schema_utils import add_column_if_missing

logger = logging.getLogger(__name__)

# 2026-07-22: mønster-baseret auto-afvisning ud over auto_dismiss_brands
# (IKEA/JYSK) - tilføjet efter en kritisk gennemgang (Claude Opus) af 167
# konkrete fund viste at den STØRSTE støjkilde var løsdele (gavl/stel/
# lameller/betræk), ikke mærke. Disse regler beskyttes af
# auto_dismiss_whitelist_keywords (se config.yaml) - IKKE af
# auto_dismiss_brands-tjekket, som stadig gælder ubetinget.
#
# Alle mønstre er FORANKREDE (^) eller kræver hele ord (\b) - bevidst
# konservative, så de kun rammer annoncer der reelt KUN er løsdelen, ikke en
# hel seng der blot NÆVNER delen (fx "Dobbeltseng inkl. madrasser og
# topmadras" skal IKKE ramt af løsdele- eller topmadras-reglen).
_LOOSE_PART_PATTERNS = [
    (re.compile(r"^(senge\s*gavl|hovedgavl|sengegærde)\b", re.I), "løsdel (gavl)"),
    (
        re.compile(
            r"^(senge\s*stel|senge\s*ramme|senge\s*bund|elevationsbund|senge\s*lameller|lameller)\b",
            re.I,
        ),
        "løsdel (stel/ramme/lameller)",
    ),
    (re.compile(r"(madras\s*)?betræk", re.I), "løsdel (betræk)"),
]
_LAGEN_PATTERN = re.compile(r"\blagen\b", re.I)
_SEEKING_PATTERN = re.compile(r"^søger\b", re.I)
_TOPMADRAS_ALONE_PATTERN = re.compile(r"^top\s*madras\b", re.I)
_SENG_PATTERN = re.compile(r"\bseng\w*\b", re.I)  # seng/senge/sengen/senges osv.

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
    """Returnerer (dismissed, reason). Rækkefølge er bevidst:

    0. force_dismiss_reason (config.yaml: "JYSK (mærke-ID, altid afvist)") -
       målet søger via DBA's EGET mærke-ID (brand=), ikke titel-tekst, netop
       fordi sælgere ikke altid skriver mærket i titlen (se targetets
       kommentar i config.yaml for et konkret eksempel). Alt herfra afvises
       ubetinget, FØR noget som helst andet tjekkes.
    1. skip_auto_dismiss (config.yaml: "Valevåg (IKEA)", som bevidst SØGER
       efter en IKEA-model) - undtager målet fra ALT nedenfor, ubetinget.
    2. auto_dismiss_brands (IKEA/JYSK titel-tekst-match) - ubetinget, IKKE
       beskyttet af whitelisten i punkt 3 (mærke-kvalitet er en anden akse
       end løsdel-mønstrene, og de to bør aldrig kunne modsige hinanden i
       samme titel).
    3. Whitelist: et ønske-mærke ELLER en tydelig "seng"+"madras"-kombination
       forhindrer punkt 4's mønster-regler (men IKKE punkt 2's mærke-tjek).
    4. Mønster-regler (løsdele/lagen/søger/topmadras-alene).
    """
    targets_by_name = {t["name"]: t for t in config.get("targets", [])}
    target_cfg = targets_by_name.get(target_name, {})
    if target_cfg.get("force_dismiss_reason"):
        return True, target_cfg["force_dismiss_reason"]
    if target_cfg.get("skip_auto_dismiss"):
        return False, None

    title_lower = title.lower()

    for brand in config.get("auto_dismiss_brands", []):
        if brand.lower() in title_lower:
            return True, f"auto:{brand.lower()}"

    # Fysisk størrelsesbegrænsning - ubetinget som mærke-tjekket ovenfor,
    # IKKE beskyttet af whitelisten nedenfor (forkert størrelse passer ikke,
    # uanset hvor godt mærket er).
    for size in config.get("auto_dismiss_sizes", []):
        if re.search(rf"(?<!\d){size}(?!\d)", title):
            return True, f"auto:størrelse-{size}"

    whitelist = config.get("auto_dismiss_whitelist_keywords", [])
    if any(w.lower() in title_lower for w in whitelist):
        return False, None
    if _SENG_PATTERN.search(title) and "madras" in title_lower:
        return False, None

    for pattern, reason in _LOOSE_PART_PATTERNS:
        if pattern.search(title):
            return True, f"auto:{reason}"

    if _LAGEN_PATTERN.search(title):
        return True, "auto:lagen"

    if _SEEKING_PATTERN.search(title):
        return True, "auto:søges-annonce"

    if _TOPMADRAS_ALONE_PATTERN.search(title) and not _SENG_PATTERN.search(title):
        return True, "auto:topmadras-alene"

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
