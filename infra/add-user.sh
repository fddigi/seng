#!/usr/bin/env bash
# Creates (or resets) the project's admin user, in one of two modes.
#
#   ./infra/add-user.sh                            # --secret-mode (v1 default)
#   ./infra/add-user.sh --secret-mode [--username <name>]
#   ./infra/add-user.sh --table-mode <username>
#
# --secret-mode (default, v1): generates a random password, hashes it, and sets
#   ADMIN_USER / ADMIN_PW_HASH as Cloudflare Worker secrets via `wrangler secret
#   put`. worker/src/index.ts's POST /login checks against these two secrets
#   directly. The `users` table (worker/migrations/0001_init.sql) exists from v1
#   onwards but is unused in this mode.
#
# --table-mode <username>: generates a random password, hashes it, and would
#   INSERT/UPDATE a row in the project's `users` table in Turso (parameter-bound,
#   via the same libsql-client SDK used everywhere else in this repo - never
#   hand-rolled HTTP). Moving the Worker's /login handler from secret-mode to a
#   `users` table lookup is a small, deliberate follow-up change, not automatic -
#   the schema is simply ready for it from day one.
#
# Password reset = run this script again for the same user; it overwrites the
# stored hash. That IS the reset flow. There is no password-reset email and no
# 2FA - a deliberate trade-off for hobby-scale, single-admin projects.
#
# The freshly generated password is printed to the terminal EXACTLY ONCE and is
# never written to disk or logged anywhere else - save it in a password manager
# immediately.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

MODE="secret-mode"
USERNAME="admin"
TABLE_MODE_DEMO_DB="${REPO_ROOT}/data/table_mode_users_demo.db"

usage() {
  cat <<'EOF'
Usage:
  add-user.sh [--secret-mode] [--username <name>]
  add-user.sh --table-mode <username>

Options:
  --secret-mode         (default) set ADMIN_USER/ADMIN_PW_HASH as Worker secrets.
  --username <name>     username to use in --secret-mode (default: admin).
  --table-mode <name>   insert/update <name> in the project's `users` table.
  -h, --help            show this help and exit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --secret-mode)
      MODE="secret-mode"
      shift
      ;;
    --username)
      USERNAME="${2:?--username requires a value}"
      shift 2
      ;;
    --table-mode)
      MODE="table-mode"
      USERNAME="${2:?--table-mode requires a username}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log_error "Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

generate_password() {
  # 20-character (15 raw bytes, base64-encoded) cryptographically random password.
  openssl rand -base64 15
}

hash_password() {
  local password="$1" python_bin
  python_bin="$(resolve_python)"
  "$python_bin" "${SCRIPT_DIR}/lib/hash_password.py" "$password"
}

print_password_once() {
  local password="$1"
  echo
  echo "=================================================================="
  echo " GENERATED PASSWORD (shown once - save it in a password manager now):"
  echo
  echo "   ${password}"
  echo
  echo " This password is not stored anywhere by this script. If you lose it,"
  echo " re-run this script for the same user to generate a new one - that IS"
  echo " the password reset flow (it overwrites the stored hash)."
  echo "=================================================================="
  echo
}

run_secret_mode() {
  require_cmd openssl
  require_cmd wrangler

  log_step "add-user.sh --secret-mode (username: ${USERNAME})"
  local password hash
  password="$(generate_password)"
  hash="$(hash_password "$password")"

  log_info "Setting ADMIN_USER / ADMIN_PW_HASH as Worker secrets..."
  wrangler_secret_put "ADMIN_USER" "$USERNAME" "${REPO_ROOT}/worker"
  wrangler_secret_put "ADMIN_PW_HASH" "$hash" "${REPO_ROOT}/worker"

  print_password_once "$password"
}

run_table_mode() {
  require_cmd openssl
  local python_bin
  python_bin="$(resolve_python)"

  log_step "add-user.sh --table-mode (username: ${USERNAME})"
  local password hash
  password="$(generate_password)"
  hash="$(hash_password "$password")"

  if [[ -n "${TURSO_DATABASE_URL:-}" && -n "${TURSO_AUTH_TOKEN:-}" ]]; then
    log_info "TURSO_DATABASE_URL/TURSO_AUTH_TOKEN are set. This would run against your real Turso db:"
    log_info "  INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')"
    log_info "  ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash"
    log_info "  (parameter-bound via scraper_core.turso_client.TursoClient / @libsql/client)"
    log_warn "This template does not wire that call up automatically - it documents the exact"
    log_warn "statement so you can run it from your own admin tooling once you adopt --table-mode."
  else
    log_warn "TURSO_DATABASE_URL/TURSO_AUTH_TOKEN not set - demonstrating the same INSERT logic"
    log_warn "against a local SQLite file instead (same schema as Turso's users table):"
    log_warn "  ${TABLE_MODE_DEMO_DB}"
  fi

  "$python_bin" "${SCRIPT_DIR}/lib/local_users_demo.py" "$TABLE_MODE_DEMO_DB" "$USERNAME" "$hash" "admin"

  print_password_once "$password"
}

case "$MODE" in
  secret-mode) run_secret_mode ;;
  table-mode)  run_table_mode ;;
esac
