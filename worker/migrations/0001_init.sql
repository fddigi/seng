-- Applied once per project against its Turso database. In this template that
-- happens as part of infra/provision.sh; there is no separate migration runner.
--
-- v1 runs in "secret-mode" (see infra/add-user.sh --secret-mode) and does not
-- read from the `users` table at all - it exists from the start so a project can
-- be upgraded to --table-mode later without any API rewrite.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    role TEXT NOT NULL DEFAULT 'admin'
);

-- Bed listings, matches scraper/scraper/pipeline.py and worker/src/index.ts's
-- /api/listings endpoint. Also created by the Python scraper itself on first
-- run (CREATE TABLE IF NOT EXISTS) - kept here too so a fresh clone's Turso db
-- has the right shape even before the scraper has run once.
CREATE TABLE IF NOT EXISTS listings (
    item_key TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    title TEXT NOT NULL,
    price_dkk REAL,
    url TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    dismissed_reason TEXT,
    brand TEXT
);
