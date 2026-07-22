// API proxy Worker for this project's Turso database. One Worker per PROJECT,
// never one Worker per user. See README.md for the full auth model ("the padlock").

import { Hono } from "hono";
import { cors } from "hono/cors";

import { createSessionToken, verifyPassword } from "./auth";
import { resolveAllowedOrigin } from "./cors";
import { getDbClient } from "./db";
import { requireAuth } from "./middleware";
import { checkAndIncrementLoginAttempts } from "./rateLimit";
import type { Env, Variables } from "./types";

const app = new Hono<{ Bindings: Env; Variables: Variables }>();

// CORS locked to exactly one configurable Pages origin in production - never
// "*" - PLUS any localhost/127.0.0.1 origin for local dev (see cors.ts).
// `origin` needs env access, which in Hono is only available per-request,
// hence the wrapper. No `credentials: true` - that flag is for cookies, and
// auth here is a bearer token in an Authorization header instead (see
// middleware.ts for why).
app.use("*", async (c, next) => {
  const middleware = cors({
    origin: (requestOrigin) => resolveAllowedOrigin(requestOrigin, c.env.ALLOWED_ORIGIN),
    allowMethods: ["GET", "POST", "OPTIONS"],
    allowHeaders: ["Content-Type", "Authorization"],
  });
  return middleware(c, next);
});

app.get("/", (c) => c.json({ status: "ok" }));

app.post("/login", async (c) => {
  let body: { username?: string; password?: string };
  try {
    body = await c.req.json();
  } catch {
    body = {};
  }
  const { username, password } = body;
  if (!username || !password) {
    return c.json({ error: "username and password are required" }, 400);
  }

  const ip = c.req.header("CF-Connecting-IP") ?? "unknown";
  const { allowed } = await checkAndIncrementLoginAttempts(c.env.RATE_LIMIT_KV, ip, username);
  if (!allowed) {
    return c.json({ error: "too many login attempts - try again later" }, 429);
  }

  // v1 runs in "secret-mode": the one admin's credentials live as Worker secrets
  // (ADMIN_USER / ADMIN_PW_HASH), set by infra/add-user.sh --secret-mode. The
  // `users` table (see worker/migrations/0001_init.sql) already exists from v1
  // onwards so a project can move to --table-mode later by swapping this block
  // for a `SELECT * FROM users WHERE username = ?` lookup - no other API changes
  // needed, and a `user_id` FK can be threaded through the same way.
  if (username !== c.env.ADMIN_USER) {
    return c.json({ error: "invalid credentials" }, 401);
  }
  const valid = await verifyPassword(password, c.env.ADMIN_PW_HASH);
  if (!valid) {
    return c.json({ error: "invalid credentials" }, 401);
  }

  const maxAgeDays = Number(c.env.SESSION_TOKEN_MAX_AGE_DAYS ?? "30");
  const maxAgeSeconds = maxAgeDays * 24 * 60 * 60;
  const token = await createSessionToken(
    { sub: username, role: "admin", exp: Math.floor(Date.now() / 1000) + maxAgeSeconds },
    c.env.SESSION_HMAC_SECRET,
  );

  // Token goes in the JSON body, not a cookie - the frontend stores it in
  // localStorage and sends it back as `Authorization: Bearer <token>`. See
  // middleware.ts for why a cookie doesn't work here (Safari ITP).
  return c.json({ ok: true, username, role: "admin", token });
});

// Stateless tokens (no server-side session store) - there is nothing to
// revoke server-side, so /logout exists mainly for symmetry/future use
// (e.g. a denylist) and to require a valid token before acknowledging.
// The actual logout action is the frontend deleting its localStorage token.
app.post("/logout", requireAuth, (c) => {
  return c.json({ ok: true });
});

app.get("/api/me", requireAuth, (c) => {
  const session = c.get("session");
  return c.json({ username: session.sub, role: session.role });
});

// --- Data endpoints against the dummy `posts` table (matches
// scraper/scraper/sources/jsonplaceholder.py). Both reads and writes sit behind
// requireAuth - there is no unauthenticated API surface beyond /login and /. ---

app.get("/api/posts", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  const limit = Math.min(Number(c.req.query("limit") ?? "50") || 50, 200);
  const result = await db.execute({
    sql: "SELECT item_key, post_id, user_id, title, body, scraped_at FROM posts ORDER BY post_id LIMIT ?",
    args: [limit],
  });
  return c.json({ posts: result.rows });
});

app.get("/api/posts/:itemKey", requireAuth, async (c) => {
  const db = getDbClient(c.env);
  const itemKey = c.req.param("itemKey");
  if (!itemKey) {
    return c.json({ error: "itemKey is required" }, 400);
  }
  const result = await db.execute({
    sql: "SELECT item_key, post_id, user_id, title, body, scraped_at FROM posts WHERE item_key = ?",
    args: [itemKey],
  });
  if (result.rows.length === 0) {
    return c.json({ error: "not found" }, 404);
  }
  return c.json({ post: result.rows[0] });
});

// Write endpoint - demonstrates the "writes behind auth" half of the pattern.
// The scraper itself writes via scraper-core's delta-sync outbox, not this API;
// this exists for e.g. a future admin panel doing manual corrections.
app.post("/api/posts", requireAuth, async (c) => {
  type PostBody = {
    item_key?: string;
    post_id?: number;
    user_id?: number;
    title?: string;
    body?: string;
  };
  let body: PostBody;
  try {
    body = await c.req.json<PostBody>();
  } catch {
    body = {};
  }

  if (!body.item_key || !body.title) {
    return c.json({ error: "item_key and title are required" }, 400);
  }

  const db = getDbClient(c.env);
  await db.execute({
    sql: `INSERT INTO posts (item_key, post_id, user_id, title, body, scraped_at)
          VALUES (?, ?, ?, ?, ?, datetime('now'))
          ON CONFLICT(item_key) DO UPDATE SET
            post_id = excluded.post_id,
            user_id = excluded.user_id,
            title = excluded.title,
            body = excluded.body,
            scraped_at = excluded.scraped_at`,
    args: [body.item_key, body.post_id ?? 0, body.user_id ?? 0, body.title, body.body ?? ""],
  });

  return c.json({ ok: true });
});

export default app;
