"""Shared SQL-identifier safety helper.

Table/column names can't be parameter-bound in SQL (parameter binding only
covers VALUES, not identifiers) - every place in this package that has to
interpolate a table/column name into a SQL string (sync.py, generations.py)
allow-lists it through this same strict regex first, instead of interpolating
raw strings directly.
"""

from __future__ import annotations

import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_ident(name: str) -> str:
    """Returns `name` unchanged if it's a safe SQL identifier, else raises."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name
