// Auth middleware: verifies the signed session token on every route it's
// applied to, otherwise responds 401. Applied to every route except POST /login.
//
// Uses `Authorization: Bearer <token>`, NOT a cookie. This was a deliberate
// correction, not the original design: a cookie-based session was tried
// first and failed in real browsers. GitHub Pages (the frontend) and this
// Worker (the API) live on two entirely different top-level domains, which
// makes the session cookie a THIRD-PARTY cookie from the browser's point of
// view. Chrome accepted it with `SameSite=None; Secure`, which masked the
// problem - but Safari's Intelligent Tracking Prevention blocks ALL
// third-party cookies by default regardless of SameSite, so login appeared
// to succeed for one instant (the response arrived) and then bounced straight
// back to the login page (the follow-up request had no cookie at all). A
// bearer token stored in localStorage and sent as a header has no such
// restriction in any browser. See SCRAPING_LESSONS.md - always test login in
// Safari specifically (or with cookies explicitly blocked), not just
// Chrome/curl, before considering auth verified.

import type { Context, Next } from "hono";
import { verifySessionToken } from "./auth";
import type { Env, SessionPayload, Variables } from "./types";

export function parseBearerToken(header: string | null | undefined): string | null {
  if (!header || !header.startsWith("Bearer ")) return null;
  const token = header.slice("Bearer ".length).trim();
  return token || null;
}

// FRAMEWORK-AGNOSTIC CORE: takes only primitives (a header string, a secret
// string), returns a session payload or null. No Hono types anywhere in this
// function - a project NOT using Hono (e.g. a plain `addEventListener("fetch",
// ...)` Worker, reading `request.headers.get("Authorization")` directly) can
// call this exactly the same way requireAuth() below does, without pulling in
// Hono at all. Split out after a real project (PLAGG, plain-JS Worker)
// needed to reuse the auth *logic* while auth.ts's hashPassword/verifyPassword/
// createSessionToken/verifySessionToken were already reusable, but the OLD
// requireAuth() hard-coded Hono's Context type through the whole function,
// forcing a full rewrite instead of a straight import.
export async function authenticateRequest(
  authorizationHeader: string | null | undefined,
  hmacSecret: string,
): Promise<SessionPayload | null> {
  const token = parseBearerToken(authorizationHeader);
  if (!token) return null;
  return verifySessionToken(token, hmacSecret);
}

// THIN HONO ADAPTER around authenticateRequest() above. This is the only
// Hono-specific part - everything that actually verifies the token lives in
// the framework-agnostic function.
export async function requireAuth(
  c: Context<{ Bindings: Env; Variables: Variables }>,
  next: Next,
) {
  const session = await authenticateRequest(c.req.header("Authorization"), c.env.SESSION_HMAC_SECRET);
  if (!session) {
    return c.json({ error: "unauthorized" }, 401);
  }

  c.set("session", session);
  await next();
}
