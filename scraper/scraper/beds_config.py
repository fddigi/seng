"""Loads this project's config.yaml (DBA-mål/targets, Playwright-indstillinger).

Samme begrundelse som PA SPEAKERS' rcf_config.py: business-specifik config
(en liste af navngivne, allerede-filtrerede søge-URL'er) hører ikke hjemme i
scraper-core's generiske .env-indstillinger.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


def load_config(path: str | Path | None = None) -> dict:
    env_path = os.environ.get("BEDS_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    config_path = Path(path) if path else Path(env_path)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)
