// Pure unit tests for password hashing + session token signing. These run under
// plain Node (via vitest), not the Workers runtime - Node 20+/25 provides the same
// Web Crypto (crypto.subtle) API used in src/auth.ts, so no Miniflare is needed
// just to test this logic in isolation.
//
// IMPORTANT LIMITATION, found the hard way (see SCRAPING_LESSONS.md): these tests,
// and `wrangler dev`, do NOT enforce Cloudflare Workers' actual production
// crypto.subtle limits (e.g. the hard 100_000 PBKDF2-iteration ceiling). A
// PBKDF2_ITERATIONS value that passes every test here and works in `wrangler dev`
// can still be 100% broken in the real deployed Worker. Green tests are not proof
// that auth works in production for anything platform-limit-adjacent - only a
// real deployed Worker is.

import { describe, expect, it } from "vitest";
import {
  createSessionToken,
  hashPassword,
  verifyPassword,
  verifySessionToken,
} from "../src/auth";

describe("password hashing", () => {
  it("verifies a correct password", async () => {
    const hash = await hashPassword("correct horse battery staple");
    expect(await verifyPassword("correct horse battery staple", hash)).toBe(true);
  });

  it("rejects an incorrect password", async () => {
    const hash = await hashPassword("correct horse battery staple");
    expect(await verifyPassword("wrong password", hash)).toBe(false);
  });

  it("produces a different hash each time (random salt)", async () => {
    const a = await hashPassword("same-password");
    const b = await hashPassword("same-password");
    expect(a).not.toBe(b);
    expect(await verifyPassword("same-password", a)).toBe(true);
    expect(await verifyPassword("same-password", b)).toBe(true);
  });

  it("rejects malformed stored hashes instead of throwing", async () => {
    await expect(verifyPassword("anything", "not-a-valid-hash")).resolves.toBe(false);
    await expect(verifyPassword("anything", "")).resolves.toBe(false);
  });
});

describe("session tokens", () => {
  const secret = "test-secret-value-not-for-production";

  it("round-trips a valid token", async () => {
    const token = await createSessionToken(
      { sub: "alice", role: "admin", exp: Math.floor(Date.now() / 1000) + 3600 },
      secret,
    );
    const payload = await verifySessionToken(token, secret);
    expect(payload?.sub).toBe("alice");
    expect(payload?.role).toBe("admin");
  });

  it("rejects a token signed with a different secret", async () => {
    const token = await createSessionToken(
      { sub: "alice", role: "admin", exp: Math.floor(Date.now() / 1000) + 3600 },
      secret,
    );
    expect(await verifySessionToken(token, "a-completely-different-secret")).toBeNull();
  });

  it("rejects an expired token", async () => {
    const token = await createSessionToken(
      { sub: "alice", role: "admin", exp: Math.floor(Date.now() / 1000) - 10 },
      secret,
    );
    expect(await verifySessionToken(token, secret)).toBeNull();
  });

  it("rejects a tampered token body", async () => {
    const token = await createSessionToken(
      { sub: "alice", role: "admin", exp: Math.floor(Date.now() / 1000) + 3600 },
      secret,
    );
    const [body, signature] = token.split(".");
    const tampered = `${body}extra.${signature}`;
    expect(await verifySessionToken(tampered, secret)).toBeNull();
  });

  it("rejects a malformed token instead of throwing", async () => {
    await expect(verifySessionToken("not-a-real-token", secret)).resolves.toBeNull();
    await expect(verifySessionToken("", secret)).resolves.toBeNull();
  });
});
