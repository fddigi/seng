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
# 2026-07-23: "madress" (dobbelt-s, engelsk-paavirket stavefejl af "madras")
# fundet i item/23212140 ("Topmadress 90x200") - ingen af madras-mønstrene
# nedenfor genkendte den. Delt fragment genbruges alle steder ordet indgaar,
# saa fremtidige tilfoejelser ikke glemmer stavevarianten.
_MADRAS_FRAGMENT = r"madr(?:as|ess)"
_MADRAS_PATTERN = re.compile(_MADRAS_FRAGMENT, re.I)

# Udtrækker "bredde" fra det danske møbel-konventions-format "BREDDExLÆNGDE"
# (fx "140x200", "90×200cm") - kun brugt af target_cfg's desired_width_cm
# (se _auto_dismiss). Matcher IKKE en enkeltstående firecifret dimension
# ("1210") pga. \d{2,3}-grænsen, og kræver "x"/"×" mellem de to tal for at
# undgå falske positiver fra tilfældige tal-par i en titel.
_WIDTH_PATTERN = re.compile(r"\b(\d{2,3})\s*[x×]\s*\d{2,3}\b", re.I)

_LOOSE_PART_PATTERNS = [
    # 2026-07-23: fjernet "^"-praefiks-kravet (Opus-kritik, runde 2) - "180
    # sengegavl fra ellos"/"Blå sengegavl"/"Ellos sengegavl..." har maal/
    # farve/mærke FORAN ordet og blev aldrig fanget af det gamle, striktere
    # praefiks-krav. \b...\b matcher nu ordet uanset position i titlen.
    # \w* TILFØJET samme dag (item/22141352 "Sengegavls paneler") - den
    # AFSLUTTENDE \b krævede en ordgrænse LIGE efter "gavl", men det danske
    # genitiv-s ("gavl-S paneler") klistrer direkte på uden grænse, samme
    # problem som _SENG_PATTERN løste for det ledende \b.
    (re.compile(r"\b(senge\s*gavl\w*|hovedgavl\w*|sengegærde\w*)\b", re.I), "løsdel (gavl)"),
    (
        re.compile(
            r"\b(senge\s*stel|senge\s*ramme|senge\s*bund|elevationsbund|senge\s*lameller|lameller"
            r"|senge\s*meder|meder)\b",
            re.I,
        ),
        "løsdel (stel/ramme/lameller/meder)",
    ),
    (re.compile(rf"({_MADRAS_FRAGMENT}\s*)?betræk", re.I), "løsdel (betræk)"),
]
_LAGEN_PATTERN = re.compile(r"\blagen\b", re.I)
_SEEKING_PATTERN = re.compile(r"^søger\b", re.I)
# 2026-07-23 (Opus-kritik runde 2 + konkret hændelse: samme "Ilva juniorseng"-
# titel dukkede op igen under et NYT DBA-id, fordi kun ÉN tidligere annonce
# var afvist manuelt - aldrig som en regel). Ubetinget som størrelses-tjekket:
# en juniorseng/hundeseng er forkert PRODUKTTYPE, ikke et støj-mønster et
# ønske-mærke burde kunne "redde" - selv en Jensen-juniorseng er forkert.
_WRONG_TYPE_PATTERN = re.compile(r"\b(junior\s*seng|børneseng|hunde\s*seng|dog\s*bed)\b", re.I)
# "Rullemadras" (billig rulle-/campingmadras) tilføjet 2026-07-23 - samme
# kategori som løs topmadras: en billig, enkeltstående madras-type, ikke det
# brugeren efterspørger, medmindre den saelges som del af en hel seng.
_TOPMADRAS_ALONE_PATTERN = re.compile(
    rf"\b(top\s*{_MADRAS_FRAGMENT}|rulle{_MADRAS_FRAGMENT})\b", re.I
)
# "Lamelbund"/"lamelrist" (løs lamelbund/-rist solgt alene) tilføjet
# 2026-07-23 (item/21644515, titel udelukkende "Lamelbund") - matchede INTET
# eksisterende mønster ("senge bund" kræver "senge"-præfiks, "lameller"
# kræver flertalsformen). Samme UBETINGET-men-seng-guardet struktur som
# topmadras-alene ovenfor (IKKE i den whitelist-beskyttede _LOOSE_PART_
# PATTERNS-gruppe): en løs base er stadig ikke en seng, uanset mærke - men
# "Drømmeland elevationsseng MED lamelbund" er en hel seng og skal IKKE
# rammes (fundet ved test af denne ændring - _SENG_PATTERN-guarden er
# derfor et krav, ikke en efterfølgende finpudsning).
_LOOSE_BASE_ALONE_PATTERN = re.compile(r"\b(lamelbund|lamelrist)\b", re.I)
# "Skummadras" (billig skum-/skumgummimadras) tilføjet 2026-07-23 - brugerens
# eksplicitte "skummadrasser har ingen interesse" (item/23011729 "To
# skummadrasser for en madras' pris"). Samme UBETINGET-men-seng-guardet
# struktur som topmadras/lamelbund ovenfor: en uønsket madras-TYPE, ikke et
# mærke-spørgsmål.
_UNDESIRED_MATERIAL_PATTERN = re.compile(rf"\bskum{_MADRAS_FRAGMENT}\w*\b", re.I)
# INTET \b foran "seng": danske sammensætninger klistrer ordet paa uden
# adskillelse ("enkeltmandsSENG", "dobbeltSENG", "gaesteSENG") - et \b-krav
# foran ville aldrig kunne matche disse (ingen ord-graense mellem "ts" og
# "seng"). Fundet 2026-07-23 da "Ilva enkeltmandsseng med topmadras" fejlagtigt
# blev afvist af topmadras-reglen, fordi _SENG_PATTERN aldrig genkendte
# "enkeltmandsseng" som en seng.
_SENG_PATTERN = re.compile(r"seng\w*\b", re.I)

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
    dismissed_reason TEXT,
    brand TEXT,
    brand_manual INTEGER NOT NULL DEFAULT 0,
    image_url TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0
);
"""
TURSO_SCHEMA = LOCAL_SCHEMA

# 2026-07-23: supplement til last_seen-baseret "formodet solgt" (48t) - en
# annonce der er VÆK fra STALE_MISS_THRESHOLD scrapes i træk af samme mål er
# et stærkere/hurtigere signal end blot "under 48t gammel", især fordi
# scrape-kadencen er uregelmæssig (nogle gange under 1t mellem koersler,
# andre gange 6+). Kun talt for maal der reelt returnerede MINDST ét resultat
# denne koersel (found_keys_by_target nedenfor) - et maal der gav 0 resultater
# kan lige saa godt vaere ramt af en bot-wall som vaere reelt tomt, og vi vil
# hellere IKKE taelle en forgaeves-runde end at taelle en falsk positiv.
STALE_MISS_THRESHOLD = 3

_INSERT_SQL = """
INSERT INTO listings (item_key, target, title, price_dkk, url, first_seen, last_seen,
    dismissed, dismissed_reason, brand, image_url)
VALUES (:item_key, :target, :title, :price_dkk, :url, :first_seen, :last_seen,
    :dismissed, :dismissed_reason, :brand, :image_url)
ON CONFLICT(item_key) DO UPDATE SET
    target = excluded.target, title = excluded.title, price_dkk = excluded.price_dkk,
    url = excluded.url, last_seen = excluded.last_seen,
    image_url = excluded.image_url,
    -- 2026-07-23: brand kan nu rettes MANUELT (worker: POST .../brand,
    -- ligesom dismiss). Uden dette tjek ville næste scrape, hver gang
    -- prisen/titlen ellers ændrer sig, overskrive en manuel rettelse med
    -- den auto-genkendte (formentlig tomme) værdi igen. brand_manual = 1
    -- laases af Worker'en og røres ALDRIG af scraperen - kun sat/aflæst.
    brand = CASE WHEN brand_manual = 1 THEN brand ELSE excluded.brand END
    -- dismissed/dismissed_reason/brand_manual er BEVIDST udeladt her (se
    -- modulets docstring) - kun sat/ændret via Worker'ens manuelle endpoints,
    -- aldrig af scraperen. image_url er IKKE følsom paa samme maade (rent
    -- afledt af DBA-kortet, ingen manuel override-risiko endnu).
"""


# Til sync_pending() (scraper_core) - se dens docstring for hvorfor dette er
# nødvendigt: uden disse ville en Turso-resync (udløst af at prisen/titlen
# ændrer sig) blindt overskrive first_seen/dismissed/dismissed_reason med
# denne køsels friske værdier, og brand med den evt. tomme auto-genkendte
# værdi selv efter en manuel rettelse (brand_manual = 1). Matcher PRÆCIS den
# beskyttelse `_INSERT_SQL` ovenfor allerede giver den lokale sqlite-skrivning.
# ":brand" (IKKE "excluded.brand") - sync_pending() bygger en almindelig
# UPDATE for 'update'-operationer (se dens docstring for hvorfor), der findes
# ingen "excluded"-pseudotabel i den kontekst.
SYNC_PROTECTED_COLUMNS = {"first_seen", "dismissed", "dismissed_reason"}
SYNC_CONDITIONAL_COLUMNS = {
    "brand": "CASE WHEN brand_manual = 1 THEN brand ELSE :brand END"
}


def make_item_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _detect_brand(title: str, config: dict) -> str | None:
    """Ønske-mærke fundet i titlen, til UI-filtrering/prioritering (IKKE til
    afvisning - se brugerens eksplicitte valg 2026-07-22: manglende mærke
    skal IKKE auto-afvises, kun kunne filtreres til sin egen liste i UI'et).
    Genbruger config.yaml:auto_dismiss_whitelist_keywords - samme liste som
    allerede beskytter mønster-reglerne, nu også brugt positivt."""
    title_lower = title.lower()
    for brand in config.get("auto_dismiss_whitelist_keywords", []):
        if brand.lower() in title_lower:
            return brand
    return None


def _auto_dismiss(title: str, target_name: str, config: dict) -> tuple[bool, str | None]:
    """Returnerer (dismissed, reason). Rækkefølge er bevidst:

    0. force_dismiss_reason (config.yaml: "JYSK (mærke-ID, altid afvist)") -
       målet søger via DBA's EGET mærke-ID (brand=), ikke titel-tekst, netop
       fordi sælgere ikke altid skriver mærket i titlen (se targetets
       kommentar i config.yaml for et konkret eksempel). Alt herfra afvises
       ubetinget, FØR noget som helst andet tjekkes.
    0'. desired_width_cm (config.yaml, fx Valevåg) - tjekkes FØR skip_auto_
        dismiss (punkt 1), IKKE beskyttet af det: et mål kan slippe for
        mærke-baseret filtrering og STADIG have en ønsket bredde. Dismisser
        KUN hvis titlen faktisk angiver en bredde ("140x200") uden for
        intervallet - ingen bredde i titlen betyder "kan ikke afgøres",
        annoncen forbliver synlig. Fundet 2026-07-23: et forsøg på at løse
        dette via DBA's EGET width_cm_from/to-filter i URL'en ekskluderede
        stiltiende alle annoncer UDEN struktureret bredde-metadata, ikke kun
        de reelt forkerte - en Valevåg-annonce uden bredde i titlen forsvandt
        fra Valevåg-målet og faldt i stedet ned i IKEA-mærke-ID-målets
        ubetingede afvisning. Titel-baseret tjek har ikke dette problem.
    1. skip_auto_dismiss (config.yaml: "Valevåg (IKEA)", som bevidst SØGER
       efter en IKEA-model) - undtager målet fra ALT nedenfor, ubetinget.
    2. auto_dismiss_brands (IKEA/JYSK titel-tekst-match) - ubetinget, IKKE
       beskyttet af whitelisten i punkt 3 (mærke-kvalitet er en anden akse
       end løsdel-mønstrene, og de to bør aldrig kunne modsige hinanden i
       samme titel).
    3'. To UBETINGEDE mønstre, IKKE beskyttet af whitelisten i punkt 3 (samme
       begrundelse som størrelse): en løs topper eller forkert produkttype
       er stadig forkert uanset mærke.
       - Topmadras-ALENE (kun hvis "seng" ikke også nævnes). Fundet
         2026-07-23 via "Wonderland top madras" (item/23104939), der
         undslap fordi "wonderland" er et ønske-mærke.
       - Skummadras (samme "seng"-guard) - brugerens eksplicitte "skum-
         madrasser har ingen interesse" (item/23011729).
       - Lamelbund/lamelrist-ALENE (samme "seng"-guard). Fundet 2026-07-23
         via item/21644515 ("Lamelbund") - en hel seng der blot MEDFØLGER
         en lamelbund ("Drømmeland elevationsseng med lamelbund") skal
         stadig IKKE rammes.
       - Juniorseng/børneseng/hundeseng. Fundet 2026-07-23: en tidligere
         "Ilva juniorseng"-annonce var kun afvist MANUELT (aldrig som
         regel) - en ny annonce med identisk titel men nyt DBA-id dukkede
         derfor bare op igen.
    3. Whitelist: et ønske-mærke ELLER en tydelig "seng"+"madras"-kombination
       forhindrer punkt 4's ØVRIGE mønster-regler (men IKKE punkt 2's
       mærke-tjek, IKKE størrelse, og IKKE 3' ovenfor).
    4. Resterende mønster-regler (løsdele/lagen/søger).
    """
    targets_by_name = {t["name"]: t for t in config.get("targets", [])}
    target_cfg = targets_by_name.get(target_name, {})
    if target_cfg.get("force_dismiss_reason"):
        return True, target_cfg["force_dismiss_reason"]

    # desired_width_cm (config.yaml, fx Valevåg): tjekkes FØR skip_auto_dismiss
    # returnerer, bevidst - et mål kan søge bredt uden mærke-baseret filtrering
    # (Valevåg søger uden DBA-breddefilter, se dens kommentar i config.yaml)
    # men STADIG ville ekskludere en tydeligt forkert bredde. Kun UBETINGET
    # dismiss hvis titlen FAKTISK angiver en bredde (fx "140x200") der ligger
    # udenfor - INGEN bredde i titlen betyder "kan ikke afgøres", annoncen
    # forbliver synlig til manuel vurdering (samme "bred søgning + manuel
    # kuratering"-princip som resten af projektet).
    desired_width = target_cfg.get("desired_width_cm")
    if desired_width:
        width_match = _WIDTH_PATTERN.search(title)
        if width_match:
            width = int(width_match.group(1))
            if not (desired_width[0] <= width <= desired_width[1]):
                return True, "auto:bredde-uden-for-ønsket-interval"

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

    if _TOPMADRAS_ALONE_PATTERN.search(title) and not _SENG_PATTERN.search(title):
        return True, "auto:topmadras-alene"

    if _UNDESIRED_MATERIAL_PATTERN.search(title) and not _SENG_PATTERN.search(title):
        return True, "auto:skummadras"

    if (
        _LOOSE_BASE_ALONE_PATTERN.search(title)
        and not _SENG_PATTERN.search(title)
        and not _MADRAS_PATTERN.search(title)
    ):
        return True, "auto:lamelbund-alene"

    if _WRONG_TYPE_PATTERN.search(title):
        return True, "auto:forkert-produkttype"

    whitelist = config.get("auto_dismiss_whitelist_keywords", [])
    if any(w.lower() in title_lower for w in whitelist):
        return False, None
    if _SENG_PATTERN.search(title) and _MADRAS_PATTERN.search(title):
        return False, None

    for pattern, reason in _LOOSE_PART_PATTERNS:
        if pattern.search(title):
            return True, f"auto:{reason}"

    if _LAGEN_PATTERN.search(title):
        return True, "auto:lagen"

    if _SEEKING_PATTERN.search(title):
        return True, "auto:søges-annonce"

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
    add_column_if_missing(store.connection, "listings", "brand", "TEXT")
    add_column_if_missing(
        store.connection, "listings", "brand_manual", "INTEGER NOT NULL DEFAULT 0"
    )
    add_column_if_missing(store.connection, "listings", "image_url", "TEXT")
    add_column_if_missing(store.connection, "listings", "pinned", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(store.connection, "listings", "misses", "INTEGER NOT NULL DEFAULT 0")
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
        # Til den efterfoelgende "N forgaeves scrapes i traek"-taelling
        # (STALE_MISS_THRESHOLD) - se den loekke laengere nede for hvorfor kun
        # maal der REELT gav mindst ét resultat her taeller med.
        found_keys_by_target: dict[str, set[str]] = {}

        for raw in raw_listings:
            url = raw.get("url", "")
            if not url:
                continue
            item_key = make_item_key(url)
            title = raw.get("title", "")
            target_name = raw.get("extra", {}).get("target", "?")
            found_keys_by_target.setdefault(target_name, set()).add(item_key)
            dismissed, dismissed_reason = _auto_dismiss(title, target_name, config)
            brand = _detect_brand(title, config)

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
                "brand": brand,
                "image_url": raw.get("image_url"),
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
                # 2026-07-23: denne last_seen-opdatering skal OGSAA til Turso -
                # "upsert_if_changed" enqueuer INTET her (content uaendret), saa
                # uden dette ville Turso's last_seen staa frosset paa foerste
                # indsaettelse for evigt for enhver annonce der aldrig aendrer
                # sig - se enqueue_update()'s docstring for hvorfor.
                store.enqueue_update(TARGET_TABLE, {
                    "item_key": item_key,
                    "last_seen": now,
                })
                continue

            store.connection.execute(_INSERT_SQL, payload)
            store.connection.commit()
            changed += 1

        # "N forgaeves scrapes i traek" - kun for maal der REELT gav mindst ét
        # resultat denne koersel (found_keys_by_target's noegler). Et maal med
        # 0 resultater kan lige saa godt vaere en bot-wall som en reelt tom
        # soegning, og en forgaeves-taelling her ville kunne give falske
        # positiver for HELE maalets bestand paa én gang - se STALE_MISS_
        # THRESHOLD's kommentar.
        for target_name, found_keys in found_keys_by_target.items():
            if not found_keys:
                continue
            rows = store.connection.execute(
                "SELECT item_key, misses FROM listings WHERE target = ? AND dismissed = 0",
                (target_name,),
            ).fetchall()
            for row in rows:
                row_item_key = row["item_key"]
                if row_item_key in found_keys:
                    if row["misses"] != 0:
                        store.connection.execute(
                            "UPDATE listings SET misses = 0 WHERE item_key = ?",
                            (row_item_key,),
                        )
                        store.enqueue_update(TARGET_TABLE, {
                            "item_key": row_item_key, "misses": 0,
                        })
                else:
                    new_misses = row["misses"] + 1
                    store.connection.execute(
                        "UPDATE listings SET misses = ? WHERE item_key = ?",
                        (new_misses, row_item_key),
                    )
                    store.enqueue_update(TARGET_TABLE, {
                        "item_key": row_item_key, "misses": new_misses,
                    })
            store.connection.commit()

        logger.info("%s: %d raw, %d new/changed", source_name, raw_count, changed)
    except SourceTimeoutError:
        pass
    except Exception:
        logger.exception("%s: source failed, skipping - other sources unaffected", source_name)

    return raw_count, changed
