#!/usr/bin/env bash
# Reusable Turso Platform API helpers. Sourced (never executed directly) by
# provision.sh, and reusable by add-user.sh if/when a project ever needs
# per-tenant databases (not used in the v1 --secret-mode / --table-mode flows,
# which share the one project database - see infra/add-user.sh).
#
# This talks ONLY to Turso's *management* API (create/delete database, mint
# tokens). Actual row-level libsql traffic always goes through the official
# libsql-client SDKs (packages/scraper-core/scraper_core/turso_client.py and
# worker/src/db.ts) - never hand-rolled HTTP against /v2/pipeline.
#
# Requires env: TURSO_PLATFORM_TOKEN, TURSO_ORG. Requires: curl, jq.

TURSO_API_BASE="${TURSO_API_BASE:-https://api.turso.tech}"

turso_api() {
  # turso_api <method> <path> [json-body]
  local method="$1" path="$2" body="${3:-}"
  local curl_args=(-sS -X "$method" "${TURSO_API_BASE}${path}"
    -H "Authorization: Bearer ${TURSO_PLATFORM_TOKEN}"
    -H "Content-Type: application/json")
  if [[ -n "$body" ]]; then
    curl_args+=(-d "$body")
  fi
  curl "${curl_args[@]}"
}

turso_database_exists() {
  # turso_database_exists <db-name>  -> exit 0 if it exists, 1 otherwise
  local db_name="$1"
  local resp
  resp=$(turso_api GET "/v1/organizations/${TURSO_ORG}/databases/${db_name}" 2>/dev/null || true)
  [[ "$(echo "$resp" | jq -r '.database.Name // empty' 2>/dev/null)" == "$db_name" ]]
}

turso_create_database() {
  # turso_create_database <db-name> [group]  -- idempotent: caller should check
  # turso_database_exists first (kept as a separate check, not hidden in here,
  # so callers can log "already exists, skipping" themselves).
  local db_name="$1" group="${2:-default}"
  log_info "Creating Turso database '${db_name}' (group=${group})..."
  turso_api POST "/v1/organizations/${TURSO_ORG}/databases" \
    "{\"name\": \"${db_name}\", \"group\": \"${group}\"}"
}

turso_get_hostname() {
  # turso_get_hostname <db-name> -> prints the db hostname (for libsql:// URLs)
  local db_name="$1"
  turso_api GET "/v1/organizations/${TURSO_ORG}/databases/${db_name}" \
    | jq -r '.database.Hostname'
}

turso_create_db_token() {
  # turso_create_db_token <db-name> [expiration] -> prints a scoped auth token
  local db_name="$1" expiration="${2:-never}"
  turso_api POST "/v1/organizations/${TURSO_ORG}/databases/${db_name}/auth/tokens?expiration=${expiration}" '{}' \
    | jq -r '.jwt'
}

turso_delete_database() {
  local db_name="$1"
  log_info "Deleting Turso database '${db_name}'..."
  turso_api DELETE "/v1/organizations/${TURSO_ORG}/databases/${db_name}"
}

turso_execute_sql_file() {
  # turso_execute_sql_file <hostname> <db-auth-token> <sql-file>
  # Applies a .sql file against a Turso database via the database's OWN
  # libsql HTTP pipeline endpoint (https://<hostname>/v2/pipeline), using the
  # database-scoped token (TURSO_AUTH_TOKEN from turso_create_db_token) - NOT
  # the `turso` CLI, which needs an interactively browser-authenticated local
  # session and is unsuited to CI. This is the same wire protocol
  # worker/src/db.ts and scraper_core/turso_client.py's libsql-client SDKs
  # speak - here it's called directly because provision.sh runs in bash, not
  # a language with a libsql SDK available.
  #
  # LIMITATION: splits on `;` after stripping `--` line-comments. Does not
  # handle semicolons inside string literals. Fine for the template's own
  # straightforward CREATE TABLE-style migration; revisit with a real SQL
  # parser (or a Python/Node one-off using the official SDK) if project
  # migrations grow more complex than that.
  local hostname="$1" auth_token="$2" sql_file="$3"
  local body
  body="$(
    grep -v '^[[:space:]]*--' "$sql_file" \
      | tr '\n' ' ' \
      | sed 's/;/;\n/g' \
      | sed '/^[[:space:]]*$/d' \
      | jq -R -s -c 'split("\n") | map(select(length > 0)) | {requests: (map({type: "execute", stmt: {sql: .}}) + [{type: "close"}])}'
  )"
  curl -sS -X POST "https://${hostname}/v2/pipeline" \
    -H "Authorization: Bearer ${auth_token}" \
    -H "Content-Type: application/json" \
    -d "$body"
}
