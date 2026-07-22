"""Healthchecks.io ping helper. No-op/skip if HEALTHCHECK_URL is not configured,
so scrapers work fine locally without signing up for a monitoring account."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


def ping_success(healthcheck_url: str | None) -> None:
    _ping(healthcheck_url, suffix="")


def ping_fail(healthcheck_url: str | None) -> None:
    _ping(healthcheck_url, suffix="/fail")


def _ping(healthcheck_url: str | None, *, suffix: str) -> None:
    if not healthcheck_url:
        logger.debug("healthcheck: HEALTHCHECK_URL not set, skipping ping")
        return
    try:
        requests.get(f"{healthcheck_url}{suffix}", timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:  # network hiccups must never crash the scraper
        logger.warning("healthcheck: ping failed (%s)", exc)
