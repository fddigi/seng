from __future__ import annotations

import time

import pytest

from scraper_core.watchdog import SourceTimeoutError, run_with_timeout


def test_run_with_timeout_returns_fast_function_result():
    result = run_with_timeout(lambda: 42, timeout_seconds=1.0, source_name="fast")
    assert result == 42


def test_run_with_timeout_raises_on_slow_function():
    """Regression test: PLAGG's real production incident - a source that hangs
    must not block the caller forever."""
    def slow():
        time.sleep(2.0)
        return "too late"

    with pytest.raises(SourceTimeoutError):
        run_with_timeout(slow, timeout_seconds=0.1, source_name="slow-source")


def test_run_with_timeout_propagates_the_original_exception():
    def boom():
        raise ValueError("source-specific failure")

    with pytest.raises(ValueError, match="source-specific failure"):
        run_with_timeout(boom, timeout_seconds=1.0, source_name="failing-source")
