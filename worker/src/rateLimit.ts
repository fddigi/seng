// Simple fixed-window login rate limiter backed by Workers KV.
//
// Bind a KV namespace as RATE_LIMIT_KV in wrangler.toml, e.g.:
//
//   [[kv_namespaces]]
//   binding = "RATE_LIMIT_KV"
//   id = "<created with: wrangler kv namespace create RATE_LIMIT_KV>"
//
// This is intentionally simple (not a sliding window, no distributed locking) -
// good enough for hobby-scale traffic and free-tier KV limits.

const WINDOW_SECONDS = 15 * 60;
const MAX_ATTEMPTS_PER_WINDOW = 5;

export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
}

export async function checkAndIncrementLoginAttempts(
  kv: KVNamespace,
  ip: string,
  username: string,
): Promise<RateLimitResult> {
  const key = `login_attempts:${ip}:${username}`;
  const current = Number.parseInt((await kv.get(key)) ?? "0", 10);

  if (current >= MAX_ATTEMPTS_PER_WINDOW) {
    return { allowed: false, remaining: 0 };
  }

  // expirationTtl resets the window on the first attempt after it lapses -
  // acceptable drift for a max-5-per-15-min hobby-scale limit.
  await kv.put(key, String(current + 1), { expirationTtl: WINDOW_SECONDS });
  return { allowed: true, remaining: MAX_ATTEMPTS_PER_WINDOW - (current + 1) };
}
