#!/usr/bin/env python3
"""Local SQLite stand-in for the `users` table INSERT that --table-mode would send
to Turso. Lets `add-user.sh --table-mode` be exercised end to end (functional
demonstration + test) without a live Turso account. Schema matches
worker/migrations/0001_init.sql exactly.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    role TEXT NOT NULL DEFAULT 'admin'
);
"""


def upsert_user(db_path: str, username: str, password_hash: str, role: str = "admin") -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            """
            INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                role = excluded.role
            """,
            (username, password_hash, role),
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        print(
            "usage: local_users_demo.py <db_path> <username> <password_hash> [role]",
            file=sys.stderr,
        )
        sys.exit(1)
    db_path, username, password_hash = sys.argv[1:4]
    role = sys.argv[4] if len(sys.argv) == 5 else "admin"
    upsert_user(db_path, username, password_hash, role)
    print(f"OK: upserted user '{username}' into {db_path}")
