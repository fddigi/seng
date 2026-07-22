"""DBA.dk: Playwright, headful/headless, best-effort. Adapteret fra PA SPEAKERS'
tilsvarende dba.py-mønster (samme Schibsted-platform, samme "sf-search-ad"-markup),
men itererer FÆRDIGBYGGEDE, allerede-filtrerede gemte søge-URL'er ("targets" i
config.yaml) i stedet for at bygge en URL ud fra et enkelt søgeord - hvert mål
bærer sine egne filtre (kategori, bredde, pris, mærke-ID'er, lokation).

Fejler ALDRIG hele scriptet: bot-wall eller andre problemer logges og giver blot
en tom liste for dette mål.
"""
from __future__ import annotations

import logging
import random
import re
import time

logger = logging.getLogger("beds.dba")

BOT_WALL_MARKERS = [
    "captcha", "for mange foresp", "unusual traffic", "access denied", "er du en robot",
]


def _looks_like_bot_wall(page) -> bool:
    content = page.content().lower()
    return any(m in content for m in BOT_WALL_MARKERS)


def _parse_price(price_text: str):
    m = re.search(r"([\d\s.]+)\s*kr", price_text or "", re.I)
    if not m:
        return None, "DKK"
    amount_str = m.group(1).replace(" ", "").replace("\xa0", "").replace(".", "")
    try:
        return float(amount_str), "DKK"
    except ValueError:
        return None, "DKK"


def _parse_listing_cards(page):
    """Selectors som PA SPEAKERS' dba.py: annonce-kort er <article class="...
    sf-search-ad ...">, titel i <h2>, link i <a class="sf-search-ad-link">
    (allerede absolut URL), pris i en <div class="... font-bold ..."> som "4.600 kr"."""
    cards = page.query_selector_all("article.sf-search-ad")
    results = []
    for card in cards:
        try:
            title_el = card.query_selector("h2")
            link_el = card.query_selector("a.sf-search-ad-link")
            if not title_el or not link_el:
                continue
            title = title_el.inner_text().strip()
            price_el = card.query_selector(".font-bold")
            price_text = price_el.inner_text() if price_el else card.inner_text()
            url = link_el.get_attribute("href")
            results.append({"title": title, "price_text": price_text, "url": url})
        except Exception:
            logger.exception("DBA: kunne ikke parse et annonce-kort, springer over")
    return results


def _paged_url(base_url: str, page_num: int) -> str:
    if page_num <= 1:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}page={page_num}"


def fetch(config: dict, dry_run: bool = False) -> list[dict]:
    """Returnerer raw listings: title/price_amount/price_currency/url/extra."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("DBA: playwright er ikke installeret, springer kilden over")
        return []

    targets = config.get("targets", [])
    pw_cfg = config.get("playwright", {})
    min_delay = pw_cfg.get("min_delay_s", 3)
    max_delay = pw_cfg.get("max_delay_s", 8)
    max_pages_per_target = pw_cfg.get("max_pages_per_target", 2)
    headless = pw_cfg.get("headless", True)

    raw_listings = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="da-DK",
            )
            page = context.new_page()

            for target in targets:
                target_name = target["name"]
                base_url = target["url"]
                try:
                    for page_num in range(1, max_pages_per_target + 1):
                        url = _paged_url(base_url, page_num)
                        logger.info("DBA: henter '%s' side %d -> %s", target_name, page_num, url)
                        page.goto(url, timeout=20000)

                        try:
                            page.wait_for_selector("article.sf-search-ad", timeout=6000)
                        except Exception:
                            pass  # ingen kort dukkede op - afklares nedenfor som normalt

                        if _looks_like_bot_wall(page):
                            logger.warning(
                                "DBA: bot-wall/CAPTCHA moedt for '%s', springer maalet over "
                                "for denne koersel",
                                target_name,
                            )
                            break

                        cards = _parse_listing_cards(page)
                        if not cards:
                            break  # ingen (flere) resultater - stop paginering for dette maal

                        for card in cards:
                            amount, currency = _parse_price(card["price_text"])
                            if amount is None:
                                continue
                            raw_listings.append({
                                "title": card["title"],
                                "price_amount": amount,
                                "price_currency": currency,
                                "url": card["url"],
                                "extra": {"target": target_name, "source_page": url},
                            })

                        time.sleep(random.uniform(min_delay, max_delay))
                except Exception:
                    logger.exception(
                        "DBA: fejl under haandtering af '%s', springer over", target_name
                    )
                    continue

            context.close()
            browser.close()
    except Exception:
        logger.exception("DBA: kilden fejlede helt, springer kilden over for denne koersel")
        return []

    return raw_listings
