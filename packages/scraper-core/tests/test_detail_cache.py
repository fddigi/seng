from __future__ import annotations

import sqlite3

from scraper_core.detail_cache import DetailFetchCache


def _cache() -> DetailFetchCache:
    return DetailFetchCache(sqlite3.connect(":memory:"))


def test_missing_fields_for_never_seen_item_is_everything_required():
    cache = _cache()
    missing = cache.missing_fields(
        source="sellpy", item_key="1", required_fields={"seller_country", "shipping_price"}
    )
    assert missing == {"seller_country", "shipping_price"}


def test_mark_fetched_then_missing_fields_is_empty():
    cache = _cache()
    cache.mark_fetched(source="sellpy", item_key="1", fields={"seller_country", "shipping_price"})
    missing = cache.missing_fields(
        source="sellpy", item_key="1", required_fields={"seller_country", "shipping_price"}
    )
    assert missing == set()


def test_regression_schema_extended_after_row_already_cached():
    """Regression test: PLAGG's real production bug, hit twice. A row cached
    BEFORE a new detail field existed must be detected as missing that field
    - not silently skipped forever like a boolean 'fetched' flag would."""
    cache = _cache()
    # Phase 1: only seller_country was part of the contract when this row was fetched.
    cache.mark_fetched(source="sellpy", item_key="1", fields={"seller_country"})

    # Phase 2: shipping_price is added to the contract later (G21-style change).
    missing = cache.missing_fields(
        source="sellpy", item_key="1", required_fields={"seller_country", "shipping_price"}
    )
    assert missing == {"shipping_price"}

    # After fetching just the missing field, nothing is missing anymore.
    cache.mark_fetched(source="sellpy", item_key="1", fields={"shipping_price"})
    assert (
        cache.missing_fields(
            source="sellpy", item_key="1", required_fields={"seller_country", "shipping_price"}
        )
        == set()
    )


def test_mark_fetched_merges_rather_than_overwrites():
    cache = _cache()
    cache.mark_fetched(source="sellpy", item_key="1", fields={"seller_country"})
    cache.mark_fetched(source="sellpy", item_key="1", fields={"shipping_price"})
    assert (
        cache.missing_fields(
            source="sellpy", item_key="1", required_fields={"seller_country", "shipping_price"}
        )
        == set()
    )


def test_different_items_are_tracked_independently():
    cache = _cache()
    cache.mark_fetched(source="sellpy", item_key="1", fields={"seller_country"})
    missing = cache.missing_fields(
        source="sellpy", item_key="2", required_fields={"seller_country"}
    )
    assert missing == {"seller_country"}


def test_different_sources_with_same_item_key_are_tracked_independently():
    cache = _cache()
    cache.mark_fetched(source="sellpy", item_key="1", fields={"seller_country"})
    missing = cache.missing_fields(
        source="vinted", item_key="1", required_fields={"seller_country"}
    )
    assert missing == {"seller_country"}
