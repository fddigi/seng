import { describe, expect, it } from "vitest";
import { createSessionToken } from "../src/auth";
import { authenticateRequest, parseBearerToken } from "../src/middleware";

describe("parseBearerToken", () => {
  it("extracts the token from a well-formed Authorization header", () => {
    expect(parseBearerToken("Bearer abc.def")).toBe("abc.def");
  });

  it("returns null for a missing header", () => {
    expect(parseBearerToken(null)).toBeNull();
    expect(parseBearerToken(undefined)).toBeNull();
  });

  it("returns null for a header without the Bearer prefix", () => {
    expect(parseBearerToken("abc.def")).toBeNull();
    expect(parseBearerToken("Basic abc.def")).toBeNull();
  });

  it("returns null for 'Bearer ' with no token after it", () => {
    expect(parseBearerToken("Bearer ")).toBeNull();
    expect(parseBearerToken("Bearer    ")).toBeNull();
  });
});

describe("authenticateRequest (framework-agnostic core)", () => {
  // Regression coverage for the split described in middleware.ts's header
  // comment: this function must work with plain primitives only - no Hono
  // Context anywhere - so a non-Hono Worker (e.g. a plain-JS project) can
  // call it exactly like this, straight from `request.headers.get(...)`.
  const secret = "test-secret-value-not-for-production";

  it("returns the session payload for a valid bearer token", async () => {
    const token = await createSessionToken(
      { sub: "alice", role: "admin", exp: Math.floor(Date.now() / 1000) + 3600 },
      secret,
    );
    const session = await authenticateRequest(`Bearer ${token}`, secret);
    expect(session?.sub).toBe("alice");
  });

  it("returns null for a missing Authorization header", async () => {
    expect(await authenticateRequest(null, secret)).toBeNull();
    expect(await authenticateRequest(undefined, secret)).toBeNull();
  });

  it("returns null for a token signed with a different secret", async () => {
    const token = await createSessionToken(
      { sub: "alice", role: "admin", exp: Math.floor(Date.now() / 1000) + 3600 },
      secret,
    );
    expect(await authenticateRequest(`Bearer ${token}`, "wrong-secret")).toBeNull();
  });
});
