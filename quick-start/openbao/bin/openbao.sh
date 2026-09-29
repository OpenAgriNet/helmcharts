#!/usr/bin/env bash
#
# Run OpenBao locally in Docker Compose, and do the steps Compose cannot.
#
#     bin/openbao.sh up                 start it; on the first run, init it and
#                                       mount KV v2 at secret/
#     bin/openbao.sh seed [--env dev] [--force]
#                                       load sample-values.json into
#                                       secret/oan/<env>/<name>
#     bin/openbao.sh status             initialised? sealed? which seal?
#     bin/openbao.sh backup-key <file>  copy the unseal key out of its volume
#     bin/openbao.sh down               stop it, keep the data
#     bin/openbao.sh destroy            stop it, DELETE the data and the key
#
# The root token and recovery keys from init go to init.json beside
# docker-compose.yml (mode 0600, gitignored), or to $OPENBAO_INIT_OUT. They are
# the only copy: lose them and the only way back in is `destroy`.
#
# Needs docker with the compose plugin, and jq.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

INIT_OUT="${OPENBAO_INIT_OUT:-$ROOT/init.json}"

say() { printf '==> %s\n' "$*"; }
die() { printf '!! %s\n' "$*" >&2; exit 1; }

command -v jq >/dev/null || die "jq is required"

# `bao` inside the container. -e BAO_TOKEN with no value passes the variable
# from this environment, so the token never appears on a command line -- not in
# `ps`, not in shell history.
bao() { docker compose exec -T -e BAO_TOKEN openbao bao "$@"; }

load_token() {
  [[ -n "${BAO_TOKEN:-}" ]] && { export BAO_TOKEN; return; }
  [[ -f "$INIT_OUT" ]] || die "no BAO_TOKEN set and no $INIT_OUT -- run \`$0 up\` first, or export BAO_TOKEN"
  BAO_TOKEN="$(jq -r .root_token "$INIT_OUT")"
  export BAO_TOKEN
}

# `bao status` exits 2 while sealed or uninitialised but still prints JSON.
# No JSON means the server was not reached at all, which must not be read as
# "not initialised".
status_field() { bao status -format=json 2>/dev/null | jq -r ".$1" 2>/dev/null || true; }

wait_reachable() {
  local v=""
  for _ in $(seq 1 30); do
    v="$(status_field initialized)"
    [[ "$v" == "true" || "$v" == "false" ]] && { echo "$v"; return; }
    sleep 2
  done
  docker compose logs --tail 30 openbao >&2 || true
  die "OpenBao did not answer within 60s -- logs above"
}

wait_unsealed() {
  for _ in $(seq 1 30); do
    [[ "$(status_field sealed)" == "false" ]] && return
    sleep 2
  done
  die "OpenBao is still sealed -- is the unseal key the one the data was sealed with?"
}

cmd_up() {
  say "starting OpenBao"
  docker compose up -d

  if [[ "$(wait_reachable)" == "false" ]]; then
    [[ ! -e "$INIT_OUT" ]] || die "$INIT_OUT exists but OpenBao is not initialised -- it belongs to an older install; move it aside and re-run"
    say "initialising (first run)"
    # Into a temp file, moved into place only on success, so a failed init
    # never leaves an empty init.json that blocks the next attempt.
    local partial="${INIT_OUT}.partial.$$"
    trap 'rm -f "$partial"' EXIT
    (umask 077; bao operator init -format=json > "$partial") || die "bao operator init failed"
    mv "$partial" "$INIT_OUT"
    trap - EXIT
    echo "    root token and recovery keys written to $INIT_OUT"
  fi

  wait_unsealed
  load_token

  if ! bao secrets list -format=json | jq -e '."secret/"' >/dev/null; then
    say "mounting KV v2 at secret/"
    bao secrets enable -path=secret kv-v2 >/dev/null
  fi

  say "ready: http://127.0.0.1:${OPENBAO_PORT:-8200}/ui  (sign in with the root_token in $INIT_OUT)"
}

cmd_seed() {
  local env="dev" force=false file="$ROOT/sample-values.json"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --env) env="$2"; shift 2 ;;
      --force) force=true; shift ;;
      --file) file="$2"; shift 2 ;;
      *) die "unknown argument to seed: $1" ;;
    esac
  done
  load_token

  local name path written=0 skipped=0
  for name in $(jq -r 'keys[] | select(startswith("_") | not)' "$file"); do
    path="secret/oan/${env}/${name}"
    # Never overwrite without --force: once a real value is in, a re-run of
    # seed must not quietly put the placeholder back.
    if ! $force && bao kv get "$path" >/dev/null 2>&1; then
      echo "    exists, skipped: $path"
      skipped=$((skipped + 1))
      continue
    fi
    # The value goes in on stdin, so it is not on any command line either.
    jq -c --arg n "$name" '.[$n]' "$file" | bao kv put "$path" - >/dev/null
    echo "    wrote $path"
    written=$((written + 1))
  done
  say "seeded secret/oan/${env}/: $written written, $skipped already there"
}

cmd_status() {
  docker compose ps
  echo
  bao status || true
}

cmd_backup_key() {
  local out="${1:-}"
  [[ -n "$out" ]] || die "usage: $0 backup-key <file>"
  [[ ! -e "$out" ]] || die "$out already exists"
  (umask 077; docker compose exec -T openbao cat /openbao/unseal/unseal.key > "$out")
  say "unseal key copied to $out -- keep it with init.json, off this machine"
}

cmd_destroy() {
  echo "This deletes OpenBao's data, audit log and unseal key. Every secret in it is gone."
  read -r -p "Type 'destroy' to continue: " answer
  [[ "$answer" == "destroy" ]] || die "not destroyed"
  docker compose down -v
  [[ -e "$INIT_OUT" ]] && mv "$INIT_OUT" "$INIT_OUT.destroyed.$(date +%s)" && echo "    moved $INIT_OUT aside -- its token opens nothing now"
  say "destroyed"
}

case "${1:-help}" in
  up)          shift; cmd_up "$@" ;;
  seed)        shift; cmd_seed "$@" ;;
  status)      shift; cmd_status ;;
  backup-key)  shift; cmd_backup_key "$@" ;;
  down)        shift; docker compose down ;;
  destroy)     shift; cmd_destroy ;;
  help|-h|--help) sed -n '3,19p' "$ROOT/bin/openbao.sh" | sed 's/^# \{0,1\}//' ;;
  *)           die "unknown command: $1 (try: $0 help)" ;;
esac
