"""Structured logging setup. Uses `rich` for readable terminal output when
attached to a TTY, falls back to plain formatting otherwise (e.g. launchd logs)."""

from __future__ import annotations

import logging
import sys

from rich.logging import RichHandler


def configure_logging(level: str = "INFO") -> None:
    handler: logging.Handler
    if sys.stderr.isatty():
        handler = RichHandler(rich_tracebacks=True, show_path=False)
        fmt = "%(message)s"
    else:
        handler = logging.StreamHandler(sys.stderr)
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
