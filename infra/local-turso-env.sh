#!/usr/bin/env bash
# Fetches THIS project's Turso database URL and mints a fresh, database-scoped
# auth token, using a personal `turso auth login` CLI session - NOT
# TURSO_PLATFORM_TOKEN (the org-secret reserved for automated CI provisioning
# in infra/provision.sh). Solves the "the local scraper can't sync because
# TURSO_AUTH_TOKEN is never retrievable after bootstrap.yml runs" gap: the
# token IS used during provisioning but deliberately never logged or written
# anywhere retrievable (it's a secret) - this script mints a NEW one on
# demand instead, which is the correct way to get local access, not a
# workaround.
#
# Prerequisite (ONE-TIME PER MACHINE, not per project): `turso auth login`
# (interactive, browser-based). See README.md's "Lokal Turso-adgang" section.
#
# Usage:
#   ./infra/local-turso-env.sh            # prints TURSO_DATABASE_URL/TURSO_AUTH_TOKEN
#   ./infra/local-turso-env.sh --write    # also writes them into .env directly

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

PROJECT_NAME="${PROJECT_NAME:-$(basename "$REPO_ROOT")}"
WRITE_MODE="false"
[[ "${1:-}" == "--write" ]] && WRITE_MODE="true"

require_cmd turso

if ! turso auth whoami >/dev/null 2>&1; then
  log_error "Not logged in to the turso CLI on this machine."
  log_error "Run 'turso auth login' once (interactive, opens a browser), then re-run this script."
  exit 1
fi

log_info "Fetching Turso database URL for '${PROJECT_NAME}'..."
db_url="$(turso db show "$PROJECT_NAME" --url 2>&1)" || {
  log_error "Could not find a Turso database named '${PROJECT_NAME}'."
  log_error "Has this project been provisioned yet? (Actions tab -> Bootstrap new project)"
  exit 1
}

log_info "Minting a fresh, database-scoped auth token (safe to run repeatedly - each call mints a new, independently revocable token; it does not affect the Worker's own token)..."
auth_token="$(turso db tokens create "$PROJECT_NAME")"

if [[ "$WRITE_MODE" == "true" ]]; then
  env_file="${REPO_ROOT}/.env"
  if [[ ! -f "$env_file" ]]; then
    log_error ".env not found - run 'cp .env.example .env' first, then re-run with --write."
    exit 1
  fi
  sed -i.bak \
    -e "s|^TURSO_DATABASE_URL=.*|TURSO_DATABASE_URL=${db_url}|" \
    -e "s|^TURSO_AUTH_TOKEN=.*|TURSO_AUTH_TOKEN=${auth_token}|" \
    "$env_file"
  rm -f "${env_file}.bak"
  log_info "Updated TURSO_DATABASE_URL / TURSO_AUTH_TOKEN in .env."
else
  echo
  echo "TURSO_DATABASE_URL=${db_url}"
  echo "TURSO_AUTH_TOKEN=${auth_token}"
  echo
  log_info "Paste the two lines above into .env, or re-run with --write to update it automatically."
fi
