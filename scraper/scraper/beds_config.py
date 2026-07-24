"""Loads this project's config.yaml (DBA-mål/targets, Playwright-indstillinger).

Samme begrundelse som PA SPEAKERS' rcf_config.py: business-specifik config
(en liste af navngivne, allerede-filtrerede søge-URL'er) hører ikke hjemme i
scraper-core's generiske .env-indstillinger.

2026-07-24: targets/auto_dismiss_brands/auto_dismiss_sizes/auto_dismiss_
whitelist_keywords kan NU redigeres via frontend'ens kontrolpanel (worker's
/api/config), som skriver til Turso's `app_config`-tabel - config.yaml er
kun STARTVÆRDIEN/fallback fra før panelet fandtes. Playwright-indstillinger
(headless/delays/max_pages) er IKKE eksponeret i panelet og læses fortsat
udelukkende fra config.yaml, da de er drifts-detaljer, ikke søge-/dismiss-
kriterier brugeren har bedt om at kunne redigere.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")

# Nøjagtig de nøgler kontrolpanelet (worker's POST /api/config) kan skrive -
# alt andet i config.yaml (playwright-blokken) er urørligt for panelet.
TURSO_OVERRIDABLE_KEYS = (
    "targets",
    "auto_dismiss_brands",
    "auto_dismiss_sizes",
    "auto_dismiss_whitelist_keywords",
)

APP_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def load_config(path: str | Path | None = None) -> dict:
    env_path = os.environ.get("BEDS_CONFIG_PATH", DEFAULT_CONFIG_PATH)
    config_path = Path(path) if path else Path(env_path)
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def apply_turso_config_overrides(config: dict, turso) -> dict:
    """Overlays targets/dismiss-lister fra Turso's app_config-tabel oven på
    config.yaml's værdier, hvis brugeren har redigeret dem via kontrolpanelet.
    Falder tilbage til config.yaml's egne værdier for enhver nøgle Turso
    endnu ikke har (fx allerførste kørsel efter panelet blev indført)."""
    turso.execute(APP_CONFIG_SCHEMA)
    placeholders = ", ".join("?" for _ in TURSO_OVERRIDABLE_KEYS)
    result = turso.execute(
        f"SELECT key, value_json FROM app_config WHERE key IN ({placeholders})",
        TURSO_OVERRIDABLE_KEYS,
    )
    overridden = dict(config)
    for key, value_json in result.rows:
        overridden[key] = json.loads(value_json)
    return overridden
