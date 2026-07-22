#!/usr/bin/env bash
# Reusable Healthchecks.io Management API helpers. Sourced (never executed
# directly) by provision.sh and destroy.sh, mirroring infra/lib/turso.sh's
# style.
#
# This is entirely OPTIONAL. HEALTHCHECK_URL has always been settable by hand
# in .env (see .env.example) - this module only automates *creating* that
# check via the Healthchecks.io Management API, gated behind the optional
# HEALTHCHECKS_API_KEY org-secret. If that secret isn't set, callers should
# skip this module entirely rather than fail - see provision.sh's
# provision_healthcheck().
#
# Requires env: HEALTHCHECKS_API_KEY. Requires: curl, jq.
# API docs: https://healthchecks.io/docs/api/

HEALTHCHECKS_API_BASE="${HEALTHCHECKS_API_BASE:-https://healthchecks.io/api/v3}"

healthchecks_api() {
  # healthchecks_api <method> <path> [json-body]
  local method="$1" path="$2" body="${3:-}"
  local curl_args=(-sS -X "$method" "${HEALTHCHECKS_API_BASE}${path}"
    -H "X-Api-Key: ${HEALTHCHECKS_API_KEY}"
    -H "Content-Type: application/json")
  if [[ -n "$body" ]]; then
    curl_args+=(-d "$body")
  fi
  curl "${curl_args[@]}"
}

healthchecks_create_or_get_check() {
  # healthchecks_create_or_get_check <name> -> prints the check's ping URL.
  # Idempotent via the API's own "unique" mechanism: if a check named <name>
  # already exists, Healthchecks.io returns that existing check (HTTP 200)
  # instead of creating a duplicate (HTTP 201) - no separate exists-check
  # needed, unlike turso.sh's database-exists dance.
  local name="$1"
  healthchecks_api POST "/checks/" \
    "{\"name\": \"${name}\", \"tags\": \"scraper-boilerplate\", \"unique\": [\"name\"]}" \
    | jq -r '.ping_url'
}

healthchecks_find_check_uuid() {
  # healthchecks_find_check_uuid <name> -> prints the UUID of the first check
  # with an exact name match, or nothing if none exists.
  local name="$1"
  healthchecks_api GET "/checks/" \
    | jq -r --arg name "$name" '.checks[] | select(.name == $name) | .uuid' \
    | head -n1
}

healthchecks_delete_check() {
  # healthchecks_delete_check <name> -- best-effort; no-op if not found.
  local name="$1" uuid
  uuid="$(healthchecks_find_check_uuid "$name")"
  if [[ -n "$uuid" ]]; then
    log_info "Deleting healthchecks.io check '${name}' (uuid=${uuid})..."
    healthchecks_api DELETE "/checks/${uuid}" >/dev/null
  else
    log_info "No healthchecks.io check named '${name}' found - nothing to delete."
  fi
}
