// Turso/libSQL client for the Worker runtime. Uses the official @libsql/client
// SDK (web/http build, which works over fetch inside Workers) - no hand-rolled
// HTTP against Turso's /v2/pipeline endpoint, always parameter binding.

import { createClient, type Client } from "@libsql/client/web";
import type { Env } from "./types";

let cachedClient: Client | null = null;

export function getDbClient(env: Env): Client {
  if (!cachedClient) {
    cachedClient = createClient({
      url: env.TURSO_DATABASE_URL,
      authToken: env.TURSO_AUTH_TOKEN,
    });
  }
  return cachedClient;
}
