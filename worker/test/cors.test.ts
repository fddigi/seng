import { describe, expect, it } from "vitest";
import { resolveAllowedOrigin } from "../src/cors";

const PROD_ORIGIN = "https://myproject.github.io";

describe("resolveAllowedOrigin", () => {
  it("allows the exact configured production origin", () => {
    expect(resolveAllowedOrigin(PROD_ORIGIN, PROD_ORIGIN)).toBe(PROD_ORIGIN);
  });

  it("rejects an unrelated origin", () => {
    expect(resolveAllowedOrigin("https://evil.example.com", PROD_ORIGIN)).toBeUndefined();
  });

  it("allows localhost at any port, for local dev", () => {
    expect(resolveAllowedOrigin("http://localhost:5173", PROD_ORIGIN)).toBe(
      "http://localhost:5173",
    );
    expect(resolveAllowedOrigin("http://localhost", PROD_ORIGIN)).toBe("http://localhost");
  });

  it("allows 127.0.0.1 at any port, for local dev", () => {
    expect(resolveAllowedOrigin("http://127.0.0.1:8787", PROD_ORIGIN)).toBe(
      "http://127.0.0.1:8787",
    );
  });

  it("rejects a lookalike domain that merely contains 'localhost'", () => {
    expect(resolveAllowedOrigin("https://localhost.evil.com", PROD_ORIGIN)).toBeUndefined();
    expect(resolveAllowedOrigin("https://notlocalhost:5173", PROD_ORIGIN)).toBeUndefined();
  });

  it("returns undefined for a missing origin header", () => {
    expect(resolveAllowedOrigin(undefined, PROD_ORIGIN)).toBeUndefined();
  });
});
