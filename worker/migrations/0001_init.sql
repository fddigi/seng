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

-- Dummy example data table, matches scraper/scraper/sources/jsonplaceholder.py
-- and worker/src/index.ts's /api/posts endpoints.
CREATE TABLE IF NOT EXISTS posts (
    item_key TEXT PRIMARY KEY,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    scraped_at TEXT NOT NULL
    -- A future `owner_user_id INTEGER REFERENCES users(id)` column can be added
    -- here later (per-row ownership / multi-tenancy) without changing the API
    -- endpoints above - it would just be one more optional column.
);
