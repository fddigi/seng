#!/usr/bin/env bash
# Shared shell helpers, sourced (never executed directly) by provision.sh,
# add-user.sh and destroy.sh.

log_info()  { echo "[info]  $*"; }
log_warn()  { echo "[warn]  $*" >&2; }
log_error() { echo "[error] $*" >&2; }
log_step()  { echo; echo "==> $*"; }

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    log_error "Required command not found on PATH: $cmd"
    exit 1
  fi
}

require_env() {
  local var_name="$1"
  if [[ -z "${!var_name:-}" ]]; then
    log_error "Required environment variable not set: $var_name"
    exit 1
  fi
}

# Picks a Python 3.11+ interpreter, preferring an explicit override.
resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    echo "$PYTHON_BIN"
    return
  fi
  for candidate in python3.11 python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      echo "$candidate"
      return
    fi
  done
  log_error "No python3 interpreter found on PATH."
  exit 1
}

# Runs `wrangler secret put <name>` with the value piped in from this project's
# worker/ directory - or, if Cloudflare credentials aren't present in this shell,
# prints the command it would have run instead. This lets provision.sh/add-user.sh
# be exercised end-to-end in CI or locally without a real Cloudflare account,
# while still being the genuine provisioning path once
# CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID are exported.
wrangler_secret_put() {
  # NOTE: deliberately avoids bash arrays for the optional --name flag - macOS
  # ships bash 3.2 as /bin/bash (the very "Mac Mini" this template targets),
  # which throws "unbound variable" under `set -u` when expanding an empty
  # array. Plain string branching keeps this script portable to bash 3.2+.
  local secret_name="$1" secret_value="$2" worker_dir="$3" worker_name="${4:-}"

  if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]]; then
    log_warn "CLOUDFLARE_API_TOKEN/CLOUDFLARE_ACCOUNT_ID not set - simulating instead of calling the live Cloudflare API:"
    if [[ -n "$worker_name" ]]; then
      log_warn "  (cd ${worker_dir} && echo '***' | wrangler secret put ${secret_name} --name ${worker_name})"
    else
      log_warn "  (cd ${worker_dir} && echo '***' | wrangler secret put ${secret_name})"
    fi
    return 0
  fi

  if [[ -n "$worker_name" ]]; then
    (cd "$worker_dir" && echo "$secret_value" | wrangler secret put "$secret_name" --name "$worker_name")
  else
    (cd "$worker_dir" && echo "$secret_value" | wrangler secret put "$secret_name")
  fi
}

# Same idea for `gh secret set`: runs for real if `gh` is authenticated, otherwise
# prints what it would have done.
gh_secret_set() {
  local secret_name="$1" secret_value="$2"
  if ! gh auth status >/dev/null 2>&1; then
    log_warn "gh not authenticated - simulating: gh secret set ${secret_name} --body '***'"
    return 0
  fi
  gh secret set "$secret_name" --body "$secret_value"
}
