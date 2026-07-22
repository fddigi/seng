// Password hashing (PBKDF2-SHA256 via the Workers-runtime Web Crypto API) and
// HMAC-signed session cookies. No third-party crypto library needed.
//
// Stored hash format: "pbkdf2$<iterations>$<saltB64url>$<hashB64url>"

import type { SessionPayload } from "./types";

// 100_000, NOT a higher "more secure" number: Cloudflare Workers' actual
// production crypto.subtle enforces a HARD ceiling of 100_000 PBKDF2
// iterations (`NotSupportedError: Pbkdf2 failed: iteration counts above
// 100000 are not supported`). This was found the hard way: a previous
// 210_000 value passed every unit test (plain Node) and `wrangler dev`
// (local workerd simulation) - NEITHER enforces this limit - while every
// single login failed with a generic "invalid credentials" in the actual
// deployed Worker, because verifyPassword()'s catch-all swallowed the
// NotSupportedError and returned false, indistinguishable from a wrong
// password. Do not raise this value without re-verifying against a real
// deployed Worker, not just tests/wrangler dev - see SCRAPING_LESSONS.md.
const PBKDF2_ITERATIONS = 100_000;
const HASH_BYTE_LENGTH = 32;

function toBase64Url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  for (const byte of arr) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/");
  const pad = (4 - (padded.length % 4)) % 4;
  const binary = atob(padded + "=".repeat(pad));
  const arr = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) arr[i] = binary.charCodeAt(i);
  return arr;
}

async function pbkdf2(password: string, salt: Uint8Array, iterations: number): Promise<Uint8Array> {
  const keyMaterial = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt: salt as BufferSource, iterations },
    keyMaterial,
    HASH_BYTE_LENGTH * 8,
  );
  return new Uint8Array(bits);
}

function constantTimeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

export async function hashPassword(password: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const derived = await pbkdf2(password, salt, PBKDF2_ITERATIONS);
  return `pbkdf2$${PBKDF2_ITERATIONS}$${toBase64Url(salt)}$${toBase64Url(derived)}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const parts = stored.split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2") return false;

  const iterations = Number.parseInt(parts[1], 10);
  if (!Number.isFinite(iterations) || iterations <= 0) return false;

  try {
    const salt = fromBase64Url(parts[2]);
    const expected = fromBase64Url(parts[3]);
    const actual = await pbkdf2(password, salt, iterations);
    return constantTimeEqual(actual, expected);
  } catch {
    return false; // malformed base64url in a stored hash - never throw on bad input
  }
}

// --- Session cookies: HMAC-SHA256 signed. Payload is not secret (username/role/exp),
// so signing (integrity) is sufficient - no need for encryption. ---

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function createSessionToken(payload: SessionPayload, secret: string): Promise<string> {
  const key = await hmacKey(secret);
  const body = toBase64Url(new TextEncoder().encode(JSON.stringify(payload)));
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return `${body}.${toBase64Url(signature)}`;
}

export async function verifySessionToken(
  token: string,
  secret: string,
): Promise<SessionPayload | null> {
  const [body, signature] = token.split(".");
  if (!body || !signature) return null;

  try {
    const key = await hmacKey(secret);
    const valid = await crypto.subtle.verify(
      "HMAC",
      key,
      fromBase64Url(signature),
      new TextEncoder().encode(body),
    );
    if (!valid) return null;

    const payload = JSON.parse(new TextDecoder().decode(fromBase64Url(body))) as SessionPayload;
    if (typeof payload.exp !== "number" || payload.exp < Math.floor(Date.now() / 1000)) {
      return null; // expired
    }
    return payload;
  } catch {
    return null; // malformed token - never throw, just treat as unauthenticated
  }
}
