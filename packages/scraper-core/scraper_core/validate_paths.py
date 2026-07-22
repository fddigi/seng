"""Fails LOUDLY at startup if a configured file path doesn't exist, instead
of silently falling back to some default behaviour.

Built after a real production bug (PLAGG, found while retrofitting this
package onto an already-live project): a configured absolute path (a Google
service-account credentials file) pointed at a location that stopped
existing after a project folder move. The code's existing "credentials not
available" fallback logic couldn't tell the difference between "not
configured" and "configured but missing" - it silently degraded to a
local-only mode, undetected across several scheduled runs, because nothing
about that failure mode looked wrong enough to log an error.
"""

from __future__ import annotations

from pathlib import Path


class ConfiguredPathMissing(Exception):
    """Raised when a path a project explicitly configured does not exist."""


def validate_paths(paths: dict[str, str | Path | None]) -> None:
    """Checks that every non-empty path in `paths` actually exists on disk.

    `paths` maps a human-readable name (used in the error message) to a
    configured path value - pass None/empty string for paths that are
    legitimately optional and unset; those are skipped, not treated as
    missing. Call this once at startup, right after loading settings, for
    every file/directory path your project's config can point at (service
    account credentials, browser storage-state files, etc.) - NOT for paths
    your own code creates on first use (e.g. the local SQLite file, which
    doesn't exist yet on a first run by design).

    Raises ConfiguredPathMissing on the first missing path found - never
    returns a bool or silently continues - so a misconfiguration fails the
    run immediately and visibly instead of degrading into a fallback mode
    nobody asked for. Example:

        validate_paths({
            "GOOGLE_CREDENTIALS_FILE": settings.google_credentials_file,
            "DBA_STORAGE_STATE_FILE": settings.dba_storage_state_file,
        })
    """
    for name, value in paths.items():
        if not value:
            continue
        if not Path(value).exists():
            raise ConfiguredPathMissing(
                f"Configured path for {name!r} does not exist: {value!r}. "
                "This was explicitly configured, so treating it as "
                "'not configured' (silent fallback) would hide a real "
                "misconfiguration - fix the path or unset it entirely."
            )
