from __future__ import annotations

import pytest

from scraper_core.validate_paths import ConfiguredPathMissing, validate_paths


def test_validate_paths_passes_when_all_configured_paths_exist(tmp_path):
    f = tmp_path / "creds.json"
    f.write_text("{}")
    validate_paths({"GOOGLE_CREDENTIALS_FILE": str(f)})  # must not raise


def test_validate_paths_skips_unset_optional_paths():
    validate_paths({"OPTIONAL_FILE": None, "OPTIONAL_FILE_2": ""})  # must not raise


def test_validate_paths_regression_missing_path_fails_loudly(tmp_path):
    """Regression test: PLAGG's real production bug - a configured path that
    stopped existing (after a folder move) silently degraded to a fallback
    instead of failing. This must raise, not return False/None/silently pass."""
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(ConfiguredPathMissing, match="GOOGLE_CREDENTIALS_FILE"):
        validate_paths({"GOOGLE_CREDENTIALS_FILE": str(missing)})


def test_validate_paths_reports_the_missing_path_value(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(ConfiguredPathMissing, match=str(missing)):
        validate_paths({"SOME_FILE": str(missing)})


def test_validate_paths_checks_all_entries_not_just_the_first(tmp_path):
    good = tmp_path / "good.json"
    good.write_text("{}")
    with pytest.raises(ConfiguredPathMissing, match="MISSING_ONE"):
        validate_paths(
            {
                "GOOD_ONE": str(good),
                "MISSING_ONE": str(tmp_path / "gone.json"),
            }
        )
