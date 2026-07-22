#!/usr/bin/env bash
# Tears down EVERYTHING provisioned for this project: the Turso database (and
# with it, all its auth tokens), the Cloudflare Worker (and its secrets), its KV
# namespace, and GitHub Pages. Irreversible - the Turso database and all scraped
# data in it are gone once this runs for real.
#
# Usage:
#   ./infra/destroy.sh --yes
#
# Without --yes this only prints what it would delete (safe to run to inspect).
#
# REQUIRED ENV: same as provision.sh (TURSO_PLATFORM_TOKEN, TURSO_ORG,
# CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID). HEALTHCHECKS_API_KEY is
# optional, same as in provision.sh - if unset, the healthcheck deletion step
# is skipped (there's nothing this script provisioned to clean up).
#
# NOT executed against a real account as part of building this template - see
# README.md, section "Hvad er IKKE bygget/eksekveret i denne skabelon".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/turso.sh
source "${SCRIPT_DIR}/lib/turso.sh"
# shellcheck source=lib/healthchecks.sh
source "${SCRIPT_DIR}/lib/healthchecks.sh"

PROJECT_NAME="${PROJECT_NAME:-$(basename "$REPO_ROOT")}"
CONFIRMED="false"

for arg in "$@"; do
  case "$arg" in
    --yes) CONFIRMED="true" ;;
    -h|--help)
      echo "Usage: destroy.sh --yes   (omit --yes for a dry-run preview)"
      exit 0
      ;;
  esac
done

main() {
  log_step "Destroying ALL provisioned resources for project '${PROJECT_NAME}'"
  if [[ "$CONFIRMED" != "true" ]]; then
    log_warn "Running in DRY-RUN mode (pass --yes to actually delete anything)."
  fi

  require_cmd curl
  require_cmd jq
  require_cmd gh
  require_cmd wrangler
  require_env TURSO_PLATFORM_TOKEN
  require_env TURSO_ORG
  require_env CLOUDFLARE_API_TOKEN
  require_env CLOUDFLARE_ACCOUNT_ID

  destroy_worker
  destroy_turso_database
  destroy_pages
  destroy_healthcheck
  clear_repo_secrets

  if [[ "$CONFIRMED" == "true" ]]; then
    log_step "Destroy complete for '${PROJECT_NAME}'"
  else
    log_step "Dry-run complete - nothing was deleted. Re-run with --yes to actually destroy."
  fi
}

destroy_worker() {
  log_step "1/4 Cloudflare Worker (and its secrets + KV binding)"
  if [[ "$CONFIRMED" != "true" ]]; then
    log_info "[dry-run] would run: wrangler delete --name ${PROJECT_NAME}"
    return
  fi
  (cd "${REPO_ROOT}/worker" && wrangler delete --name "$PROJECT_NAME" --force) \
    || log_warn "Worker delete failed or Worker did not exist - continuing."
}

destroy_turso_database() {
  log_step "2/4 Turso database (deleting the db revokes all of its auth tokens)"
  if [[ "$CONFIRMED" != "true" ]]; then
    log_info "[dry-run] would delete Turso database '${PROJECT_NAME}'."
    return
  fi
  if turso_database_exists "$PROJECT_NAME"; then
    turso_delete_database "$PROJECT_NAME"
  else
    log_info "Database '${PROJECT_NAME}' does not exist - nothing to delete."
  fi
}

destroy_pages() {
  log_step "3/4 GitHub Pages"
  local repo_full_name
  repo_full_name="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

  if [[ "$CONFIRMED" != "true" ]]; then
    log_info "[dry-run] would run: gh api repos/${repo_full_name}/pages -X DELETE"
    return
  fi
  gh api "repos/${repo_full_name}/pages" -X DELETE >/dev/null 2>&1 \
    || log_warn "GitHub Pages delete failed or was not enabled - continuing."
}

destroy_healthcheck() {
  log_step "4/4 Healthchecks.io check (optional, only if HEALTHCHECKS_API_KEY is set)"
  if [[ -z "${HEALTHCHECKS_API_KEY:-}" ]]; then
    log_info "HEALTHCHECKS_API_KEY not set - skipping (nothing was auto-provisioned to clean up)."
    return
  fi
  if [[ "$CONFIRMED" != "true" ]]; then
    log_info "[dry-run] would delete healthchecks.io check named '${PROJECT_NAME}' if it exists."
    return
  fi
  healthchecks_delete_check "$PROJECT_NAME"
}

clear_repo_secrets() {
  log_step "Clearing repo secrets written by provision.sh"
  for secret in TURSO_DB_NAME CF_WORKER_NAME CLOUDFLARE_ACCOUNT_ID HEALTHCHECK_URL; do
    if [[ "$CONFIRMED" != "true" ]]; then
      log_info "[dry-run] would run: gh secret remove ${secret}"
      continue
    fi
    gh secret remove "$secret" >/dev/null 2>&1 || true
  done
}

main "$@"
