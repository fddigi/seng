#!/usr/bin/env bash
# Idempotent one-shot provisioning for a new project created from this template.
#
# WHAT THIS DOES, IN ORDER:
#   1. Creates one Turso database named after this repo (skips if it already
#      exists), mints a scoped db-auth-token, and applies
#      worker/migrations/0001_init.sql against it via `turso db shell`
#      (idempotent - the migration itself is CREATE TABLE IF NOT EXISTS).
#   2. Fills in worker/wrangler.toml's placeholders (project name, CORS
#      ALLOWED_ORIGIN computed from the GitHub Pages URL, and a KV namespace
#      created via `wrangler kv namespace create` if one doesn't already exist),
#      deploys the Cloudflare Worker, sets its TURSO_DATABASE_URL /
#      TURSO_AUTH_TOKEN / SESSION_HMAC_SECRET secrets via `wrangler secret put`
#      (ADMIN_USER/ADMIN_PW_HASH are set separately by ./infra/add-user.sh, not
#      here), and fills in frontend/config.js's API_BASE with the deployed
#      Worker's real workers.dev URL.
#   3. Checks whether GitHub Pages is enabled (idempotent) - does NOT try to
#      enable it: that requires a real user/PAT credential and is documented
#      as a one-time manual step (Settings -> Pages -> Source: "GitHub
#      Actions"). See provision_pages() for why GITHUB_TOKEN can never do this.
#   4. OPTIONAL: if HEALTHCHECKS_API_KEY is set, creates a per-project
#      healthchecks.io check via infra/lib/healthchecks.sh and captures its
#      ping URL. Skipped entirely (not an error) if the key isn't set - see
#      .env.example's HEALTHCHECK_URL comment for the manual alternative.
#   5. Writes the generated non-secret identifiers back as GitHub repo secrets
#      via `gh secret set`, so deploy.yml can find the right Worker/db on every
#      push to main.
#   6. Commits and pushes the filled-in wrangler.toml/config.js back to `main`
#      (requires `contents: write` - see bootstrap.yml) - otherwise these
#      generated values only exist on the ephemeral Actions runner and are lost
#      the moment the workflow ends. Best-effort: a push failure (e.g. running
#      this locally without push access) logs a warning, not a hard error, since
#      the actual cloud resources are already provisioned by this point.
#
# REQUIRED ENV (set as org-level GitHub secrets when run from bootstrap.yml, or
# in your own shell when run manually):
#   TURSO_PLATFORM_TOKEN   - Turso Platform API token (Turso dashboard -> Settings)
#   TURSO_ORG              - your Turso organization slug
#   CLOUDFLARE_API_TOKEN   - Cloudflare API token (Workers Scripts + KV + Pages edit)
#   CLOUDFLARE_ACCOUNT_ID  - Cloudflare account id
#
# OPTIONAL ENV:
#   HEALTHCHECKS_API_KEY   - Healthchecks.io Management API key (Project Settings
#                            -> API access). Enables automatic per-project
#                            healthcheck creation. Omit to skip that step entirely.
#
# Requires on PATH: curl, jq, gh, wrangler.
#
# Idempotent: safe to re-run. Existing resources are detected and left alone
# (Turso db, GitHub Pages); secrets are always re-set since `wrangler secret put`
# / `gh secret set` are themselves overwrite-in-place operations.
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
HEALTHCHECK_URL=""

main() {
  log_step "Provisioning project '${PROJECT_NAME}'"

  require_cmd curl
  require_cmd jq
  require_cmd gh
  require_cmd wrangler
  require_env TURSO_PLATFORM_TOKEN
  require_env TURSO_ORG
  require_env CLOUDFLARE_API_TOKEN
  require_env CLOUDFLARE_ACCOUNT_ID
  # HEALTHCHECKS_API_KEY is deliberately NOT required - see provision_healthcheck().

  provision_turso_database
  provision_worker
  provision_pages
  provision_healthcheck
  write_repo_secrets
  commit_generated_config

  log_step "Provisioning complete for '${PROJECT_NAME}'"
  if [[ -n "$HEALTHCHECK_URL" ]]; then
    log_info "Healthcheck URL (also written to the HEALTHCHECK_URL repo secret): ${HEALTHCHECK_URL}"
  fi
  log_info "Next step: ./infra/add-user.sh to create the first admin login."
}

provision_turso_database() {
  log_step "1/5 Turso database"
  if turso_database_exists "$PROJECT_NAME"; then
    log_info "Database '${PROJECT_NAME}' already exists - skipping create (idempotent)."
  else
    turso_create_database "$PROJECT_NAME" >/dev/null
  fi

  local hostname
  hostname="$(turso_get_hostname "$PROJECT_NAME")"
  TURSO_DATABASE_URL="libsql://${hostname}"
  TURSO_AUTH_TOKEN="$(turso_create_db_token "$PROJECT_NAME")"
  log_info "Turso database URL: ${TURSO_DATABASE_URL}"

  local migration_file="${REPO_ROOT}/worker/migrations/0001_init.sql"
  if [[ -f "$migration_file" ]]; then
    log_info "Applying ${migration_file} to '${PROJECT_NAME}' via Turso's HTTP pipeline API..."
    local migration_response
    migration_response="$(turso_execute_sql_file "$hostname" "$TURSO_AUTH_TOKEN" "$migration_file")"
    if echo "$migration_response" | jq -e '.results[]? | select(.type == "error")' >/dev/null 2>&1; then
      log_warn "Migration may have failed - full response: ${migration_response}"
    fi
  else
    log_warn "No worker/migrations/0001_init.sql found - skipping schema migration."
  fi
}

kv_lookup_id_by_title() {
  # kv_lookup_id_by_title <title> -> prints the namespace id, or nothing if
  # not found. The one reliable way to get a KV namespace id: `wrangler kv
  # namespace list` is JSON and stable across wrangler versions, unlike
  # `create`'s human-readable stdout (which can contain non-interactive
  # "Using fallback value..." prompt text in CI, breaking naive grep parsing).
  local title="$1"
  wrangler kv namespace list 2>/dev/null | jq -r --arg t "$title" '.[] | select(.title == $t) | .id' | head -n1
}

provision_worker() {
  log_step "2/5 Cloudflare Worker"
  local wrangler_toml="${REPO_ROOT}/worker/wrangler.toml"
  local config_js="${REPO_ROOT}/frontend/config.js"
  local repo_full_name owner allowed_origin

  repo_full_name="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
  owner="${repo_full_name%%/*}"
  # Portable lowercase (not ${owner,,} - that's bash 4+, macOS ships bash 3.2).
  owner="$(echo "$owner" | tr '[:upper:]' '[:lower:]')"
  allowed_origin="https://${owner}.github.io"

  log_info "Filling in wrangler.toml placeholders (name, ALLOWED_ORIGIN)..."
  sed -i.bak \
    -e "s|REPLACE_WITH_PROJECT_NAME|${PROJECT_NAME}|" \
    -e "s|https://REPLACE_WITH_GITHUB_USERNAME.github.io|${allowed_origin}|" \
    "$wrangler_toml"
  rm -f "${wrangler_toml}.bak"

  log_info "Ensuring KV namespace for RATE_LIMIT_KV exists..."
  local kv_title="${PROJECT_NAME}-RATE_LIMIT_KV" kv_id
  kv_id="$(kv_lookup_id_by_title "$kv_title")"
  if [[ -z "$kv_id" ]]; then
    log_info "No existing KV namespace named '${kv_title}' - creating one..."
    # NOTE: the namespace's title/name is a POSITIONAL argument to this
    # command, not a --title flag (verified against the exact pinned wrangler
    # version's own --help output - an earlier version of this script assumed
    # a --title flag that does not exist in this CLI at all, which failed
    # hard in CI: "Unknown argument: title"). Passing the prefixed title
    # directly here is also what makes our own reuse-lookup above able to
    # find this namespace again on a future re-run - plain `RATE_LIMIT_KV`
    # (no project prefix) would not match `kv_title`.
    # We deliberately do NOT parse `create`'s human-readable stdout for the id
    # (it can contain non-interactive-fallback prompt text that breaks naive
    # grep parsing in CI) - instead re-run the same reliable list|jq lookup
    # used for the reuse-check above, now that the namespace exists.
    (cd "${REPO_ROOT}/worker" && wrangler kv namespace create "$kv_title") >/dev/null
    kv_id="$(kv_lookup_id_by_title "$kv_title")"
  else
    log_info "Found existing KV namespace '${kv_title}' (id=${kv_id}) - reusing (idempotent)."
  fi
  if [[ -z "$kv_id" ]]; then
    log_error "Could not create/find KV namespace '${kv_title}' - see wrangler output above."
    exit 1
  fi
  sed -i.bak -e "s|REPLACE_WITH_KV_NAMESPACE_ID|${kv_id}|" "$wrangler_toml"
  rm -f "${wrangler_toml}.bak"

  log_info "Deploying Worker '${PROJECT_NAME}' via wrangler..."
  (cd "${REPO_ROOT}/worker" && wrangler deploy)

  log_info "Setting Worker secrets (idempotent - wrangler secret put overwrites in place)..."
  wrangler_secret_put "TURSO_DATABASE_URL" "$TURSO_DATABASE_URL" "${REPO_ROOT}/worker" "$PROJECT_NAME"
  wrangler_secret_put "TURSO_AUTH_TOKEN" "$TURSO_AUTH_TOKEN" "${REPO_ROOT}/worker" "$PROJECT_NAME"

  local session_secret="${SESSION_HMAC_SECRET:-}"
  if [[ -z "$session_secret" ]]; then
    session_secret="$(openssl rand -base64 32)"
  fi
  wrangler_secret_put "SESSION_HMAC_SECRET" "$session_secret" "${REPO_ROOT}/worker" "$PROJECT_NAME"

  log_info "ADMIN_USER / ADMIN_PW_HASH are set separately: run ./infra/add-user.sh next."

  log_info "Filling in frontend/config.js's API_BASE with the deployed Worker URL..."
  local subdomain worker_url
  subdomain="$(curl -sS "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/subdomain" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" | jq -r '.result.subdomain // empty')"
  if [[ -n "$subdomain" ]]; then
    worker_url="https://${PROJECT_NAME}.${subdomain}.workers.dev"
    sed -i.bak \
      -e "s|https://REPLACE_WITH_WORKER_SUBDOMAIN.workers.dev|${worker_url}|" \
      "$config_js"
    rm -f "${config_js}.bak"
    log_info "frontend/config.js API_BASE set to: ${worker_url}"
  else
    log_warn "Could not determine the account's workers.dev subdomain - frontend/config.js still has a placeholder." \
      "Fill in API_BASE manually (see README step 7)."
  fi
}

provision_pages() {
  log_step "3/5 GitHub Pages"
  local repo_full_name
  repo_full_name="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

  if gh api "repos/${repo_full_name}/pages" >/dev/null 2>&1; then
    log_info "GitHub Pages already enabled - skipping (idempotent)."
    return
  fi

  # Deliberately does NOT attempt to create the Pages site via the API at
  # all from HERE (an earlier version of this script tried a POST using
  # GITHUB_TOKEN and always failed). Confirmed impossible on two independent
  # counts, verified with an authenticated user gh session against this exact
  # API call: (1) creating/enabling a repo's Pages site is an administrative
  # action GitHub only permits for a real user/PAT credential - GITHUB_TOKEN
  # (this script's identity when run FROM bootstrap.yml in CI) gets a 403
  # ("Resource not accessible by integration") no matter what `permissions:`
  # a workflow declares, and no matter the org/repo "Workflow permissions"
  # setting; (2) even WITH a fully privileged credential, the classic
  # branch+path serving method only accepts "/" or "/docs" as source.path -
  # "/frontend" (this template's actual layout) is rejected outright (422
  # "is not a possible value"). deploy.yml's deploy-pages job uses the
  # modern Actions-based method instead, which has neither restriction and
  # works fine with the plain GITHUB_TOKEN for the deploy itself - only the
  # one-time Pages-site *creation* needs a real credential.
  #
  # This is NOT "a human must click a button" - it's "must run outside
  # GITHUB_TOKEN's ephemeral CI context". Deliberately NOT solved by storing
  # a more powerful PAT as a persistent org-secret either (that trades a
  # well-understood, ephemeral limitation for a standing, broader-than-
  # GITHUB_TOKEN credential - a worse trade). Instead: run
  # ./infra/finish-bootstrap-locally.sh once, locally, with whatever already-
  # authenticated `gh` session exists on the provisioning machine (a human's,
  # or an agent's - both work identically, no new credential needed). See
  # docs/SCRAPING_LESSONS.md.
  log_warn "GitHub Pages is not yet enabled for this repo, and cannot be enabled" \
    "from here (GITHUB_TOKEN can never create a Pages site, under any configuration)."
  log_warn "Run './infra/finish-bootstrap-locally.sh' once, locally, using your own" \
    "authenticated gh session (human or agent, either works) - it also finishes" \
    "the repo-secrets step below if that fails too. Idempotent, safe to re-run."
}

provision_healthcheck() {
  log_step "4/5 Healthchecks.io check (optional)"
  if [[ -z "${HEALTHCHECKS_API_KEY:-}" ]]; then
    log_info "HEALTHCHECKS_API_KEY not set - skipping automatic healthcheck creation."
    log_info "You can still set HEALTHCHECK_URL manually in .env (see .env.example)."
    return
  fi
  log_info "Creating (or finding existing) healthchecks.io check '${PROJECT_NAME}'..."
  HEALTHCHECK_URL="$(healthchecks_create_or_get_check "$PROJECT_NAME")"
  if [[ -z "$HEALTHCHECK_URL" || "$HEALTHCHECK_URL" == "null" ]]; then
    log_warn "Healthchecks.io API call did not return a ping_url - check HEALTHCHECKS_API_KEY. Continuing without it."
    HEALTHCHECK_URL=""
    return
  fi
  log_info "Healthcheck ping URL: ${HEALTHCHECK_URL}"
}

write_repo_secrets() {
  log_step "5/5 Writing generated identifiers back as repo secrets"
  # Each call is allowed to fail without aborting the script (note the `||`)
  # - see the warning block below for why, and why commit_generated_config()
  # must still run regardless of this step's outcome.
  local any_failed="false"
  gh_secret_set "TURSO_DB_NAME" "$PROJECT_NAME" || any_failed="true"
  gh_secret_set "CF_WORKER_NAME" "$PROJECT_NAME" || any_failed="true"
  gh_secret_set "CLOUDFLARE_ACCOUNT_ID" "$CLOUDFLARE_ACCOUNT_ID" || any_failed="true"
  if [[ -n "$HEALTHCHECK_URL" ]]; then
    gh_secret_set "HEALTHCHECK_URL" "$HEALTHCHECK_URL" || any_failed="true"
  fi
  log_info "TURSO_AUTH_TOKEN / SESSION_HMAC_SECRET stay Worker-only secrets, not duplicated as repo secrets."

  if [[ "$any_failed" == "true" ]]; then
    # Confirmed against GitHub's own workflow-syntax documentation: there is
    # no "secrets" permission scope, in any form - GITHUB_TOKEN can NEVER
    # write or manage repository Actions secrets, under any `permissions:`
    # configuration or org/repo "Workflow permissions" setting. This is a
    # hard platform limitation, not a bug in this script, and it would have
    # crashed the entire remaining script (including commit_generated_config()
    # below) under set -e before this fix.
    #
    # Deliberately NOT solved with a stored PAT/org-secret here either (same
    # reasoning as provision_pages() above: a persistent, broader-than-
    # GITHUB_TOKEN credential is a worse trade than an occasional local
    # command). Use the machine's existing authenticated gh session instead.
    log_warn "Could not write one or more repo secrets via GITHUB_TOKEN (expected -" \
      "GITHUB_TOKEN can never write repo Actions secrets, under any configuration)."
    log_warn "Run './infra/finish-bootstrap-locally.sh' once, locally, using your own" \
      "authenticated gh session (human or agent, either works) - it also finishes" \
      "the GitHub Pages step above if that failed too. Idempotent, safe to re-run."
  fi
}

commit_generated_config() {
  log_step "Committing generated wrangler.toml/config.js back to main"
  (
    cd "$REPO_ROOT"
    git config user.name "scraper-boilerplate-bot" 2>/dev/null || true
    git config user.email "actions@users.noreply.github.com" 2>/dev/null || true
    git add worker/wrangler.toml frontend/config.js
    if git diff --cached --quiet; then
      log_info "No generated-config changes to commit (already up to date)."
    elif git commit -m "provision.sh: fill in generated Worker/Pages config" >/dev/null && git push; then
      log_info "Pushed generated wrangler.toml/config.js to main."
    else
      log_warn "Could not commit/push generated config (often the same org-level" \
        "'Workflow permissions' restriction as GitHub Pages above, if that also failed)." \
        "Do it manually: git add worker/wrangler.toml frontend/config.js &&" \
        "git commit -m 'fill in config' && git push"
    fi
  ) || log_warn "commit_generated_config failed - the cloud resources above are still provisioned correctly."
}

main "$@"
