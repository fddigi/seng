// CORS origin resolution: production is locked to exactly one configured
// origin (never "*"), but ALSO allows any localhost/127.0.0.1 origin
// regardless of port, so local development (`wrangler dev` + a frontend
// server on whatever port) works without editing ALLOWED_ORIGIN back and
// forth between a real deployment and local testing. A single fixed
// ALLOWED_ORIGIN with no dev exception breaks local dev entirely - this was
// a real point of friction found while retrofitting a live project (PLAGG).

const LOCALHOST_ORIGIN_RE = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/;

export function resolveAllowedOrigin(
  requestOrigin: string | undefined,
  allowedOrigin: string,
): string | undefined {
  if (!requestOrigin) return undefined;
  if (requestOrigin === allowedOrigin) return requestOrigin;
  if (LOCALHOST_ORIGIN_RE.test(requestOrigin)) return requestOrigin;
  return undefined;
}
