// Cloudflare Worker environment bindings. Variable names must match wrangler.toml
// `[vars]` and the `wrangler secret put <NAME>` calls documented there, and stay
// consistent with the TURSO_DATABASE_URL / TURSO_AUTH_TOKEN names used in
// packages/scraper-core and .env.example.
export interface Env {
  TURSO_DATABASE_URL: string;
  TURSO_AUTH_TOKEN: string;
  SESSION_HMAC_SECRET: string;
  // v1 "secret-mode" credentials (see infra/add-user.sh --secret-mode).
  ADMIN_USER: string;
  ADMIN_PW_HASH: string;
  ALLOWED_ORIGIN: string;
  SESSION_TOKEN_MAX_AGE_DAYS?: string;
  RATE_LIMIT_KV: KVNamespace;
}

export interface SessionPayload {
  sub: string; // username
  role: string;
  exp: number; // unix seconds
}

export interface Variables {
  session: SessionPayload;
}
