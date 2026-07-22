#!/usr/bin/env bash
# Finishes the two provisioning steps GITHUB_TOKEN can never perform from
# within bootstrap.yml, no matter what `permissions:` it's granted: activating
# GitHub Pages, and writing repo-level Actions secrets. Both are GitHub
# administrative actions restricted to a real user/PAT credential - this is
# NOT a "someone must click a button in the browser" limitation, just a "must
# run outside the ephemeral per-run CI token" one. Run this ONCE per new
# project, locally, by whoever/whatever already has an authenticated `gh`
# session on this machine - a human, or an agent driving the provisioning
# process. No new credential needs to be provisioned or stored anywhere for
# this: the existing `gh auth login` session already has enough scope.
#
# Deliberately does NOT use a stored PAT/org-secret for this (a persistent,
# broader-than-GITHUB_TOKEN credential would be a real, ongoing security cost
# to eliminate one occasional local command - see docs/SCRAPING_LESSONS.md).
#
# Idempotent: safe to re-run. Both steps individually skip/overwrite in place
# if already done.
#
# Usage: ./infra/finish-bootstrap-locally.sh
# (run from the project repo root, any time after bootstrap.yml has run once)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

PROJECT_NAME="${PROJECT_NAME:-$(basename "$REPO_ROOT")}"

main() {
  require_cmd gh
  if ! gh auth status >/dev/null 2>&1; then
    log_error "gh is not authenticated on this machine - run 'gh auth login' first."
    exit 1
  fi

  activate_pages
  write_secrets

  log_step "Done"
  log_info "Both steps are idempotent - safe to re-run any time, e.g. after re-running bootstrap.yml."
}

activate_pages() {
  log_step "GitHub Pages"
  local repo_full_name
  repo_full_name="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

  if gh api "repos/${repo_full_name}/pages" >/dev/null 2>&1; then
    log_info "Already enabled - skipping (idempotent)."
    return
  fi

  # build_type=workflow is the Actions-based method deploy.yml's deploy-pages
  # job expects - NOT the classic branch+path method, which rejects
  # "/frontend" as a source.path outright regardless of credential.
  gh api "repos/${repo_full_name}/pages" -X POST -f build_type=workflow >/dev/null
  log_info "GitHub Pages enabled (Actions-based deployment)."
}

write_secrets() {
  log_step "Repo secrets"
  require_env CLOUDFLARE_ACCOUNT_ID

  gh_secret_set "TURSO_DB_NAME" "$PROJECT_NAME"
  gh_secret_set "CF_WORKER_NAME" "$PROJECT_NAME"
  gh_secret_set "CLOUDFLARE_ACCOUNT_ID" "$CLOUDFLARE_ACCOUNT_ID"
  log_info "TURSO_DB_NAME / CF_WORKER_NAME / CLOUDFLARE_ACCOUNT_ID written."
  log_info "HEALTHCHECK_URL not re-derived here (optional) - set it manually with" \
    "'gh secret set HEALTHCHECK_URL --body <url>' if you want it."
}

main "$@"
