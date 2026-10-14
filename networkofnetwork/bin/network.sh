#!/usr/bin/env bash
# Stand up one whole OAN network, locally, by name.
#
# Run it again with a different name and you have two, side by side, able to
# reach each other -- which is the whole point. Everything that differs between
# them comes from that network's own .env, generated here, so this script is the
# one place that knows how to point the shared compose file at one of them:
#
#   COMPOSE_PROJECT_NAME  which project the containers belong to
#   COMPOSE_ENV_FILES     which .env compose itself reads
#   QUICKSTART_ROOT       where setup.py reads .env and writes keys and configs
#
# Usage:  network.sh <command> <networkId>
#
#   up               certs, env, config, containers, registry seed, front door
#   setup            re-seed the registry and re-render configs only
#   host-descriptor  sign the two documents this network hosts at its own domain
#   admit            agree to deal with another network: admit <us> <them>
#   down             stop it, keep the data
#   destroy          stop it and delete the data, and release its ports
#   teardown         destroy EVERY deployed network (asks first)
#   ps|logs          what it is doing
#   ls               every network on this machine and the ports it holds
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
STATE="$ROOT/networks"
COMPOSE="$ROOT/docker-compose.yml"

# The bridge every network shares. Created once, outside any project, because a
# project-owned network would vanish with whichever project was torn down first.
FEDERATION_BRIDGE=oan-federation

die() { echo "network: $*" >&2; exit 1; }

need_network() {
  [ -n "${1:-}" ] || die "which network? e.g. make network NETWORK_ID=bharatvistar.oan.local"
}

# project turns a network id into a compose project name. Docker allows a narrow
# set of characters, and a network id is a domain -- so the dots go.
project() { echo "oan-${1//./-}"; }

network_dir() { echo "$STATE/$1"; }

compose() {
  local network="$1"; shift
  COMPOSE_PROJECT_NAME="$(project "$network")" \
  COMPOSE_ENV_FILES="$(network_dir "$network")/.env" \
    docker compose -f "$COMPOSE" "$@"
}

ensure_bridge() {
  docker network inspect "$FEDERATION_BRIDGE" >/dev/null 2>&1 && return
  echo "network: creating the shared bridge $FEDERATION_BRIDGE"
  docker network create "$FEDERATION_BRIDGE" >/dev/null
}

cmd_up() {
  local network="$1"
  ensure_bridge
  # Every directory the compose file bind-mounts, created BEFORE anything
  # starts. Docker creates a missing bind-mount source as a ROOT-OWNED
  # directory, and then nothing in this script can write the file that was
  # supposed to be there -- including on every later run.
  mkdir -p "$(network_dir "$network")"/{.well-known,config,tls,keys}
  "$HERE/network-certs.sh" "$network"
  python3 "$HERE/network-env.py" "$network"
  # The core first, and the adapter deliberately NOT yet: its config does not
  # exist until setup renders it, and docker creates a DIRECTORY at a bind-mount
  # source that is missing. The adapter would then fail with "adapter.yaml is a
  # directory" on every restart, for ever, with nothing saying why.
  echo "network: starting $network"
  compose "$network" up -d registry-postgres registry-keycloak registry-service \
                           discovery-postgres discovery-service mock-imd mock-agmarknet
  cmd_setup "$network"
  echo
  echo "network: $network is up"
  python3 "$HERE/network-ports.py" ports "$network" | sed 's/^/  /'
}

cmd_setup() {
  local network="$1"
  echo "network: seeding the registry for $network"
  # Keys, the registry seed and the schema registry. It writes into the
  # network's own directory, which is what QUICKSTART_ROOT selects.
  QUICKSTART_ROOT="$(network_dir "$network")" python3 "$HERE/setup.py"
  # The adapter config, which setup.py deliberately does not render: this
  # deployment runs ONE adapter and the three-role templates do not exist here.
  python3 "$HERE/render-adapter.py" "$network"
  # Now the config exists, so the adapter can start. Recreated rather than
  # started, because a container reads its config once and this is also how a
  # config change is applied.
  compose "$network" up -d --force-recreate adapter wellknown
}

cmd_host_descriptor() {
  local network="$1"
  local dir; dir="$(network_dir "$network")"
  local descriptor="$dir/.well-known/openagrinet"

  # The state lives with the checkout that brought the network up. Without
  # this, the .env read below fails under set -e and make exits with no word.
  [ -f "$dir/.env" ] || die "$network is not set up in $STATE -- run 'make network NETWORK_ID=$network' from this checkout first"

  # A revision must only ever INCREASE -- another network refuses a descriptor
  # offering one at or below what it already holds, which is what stops a
  # withdrawn declaration being replayed as current. So it is derived from the
  # last one published rather than asked for: publishing twice without changing
  # anything would otherwise be refused by everyone who already has it.
  local revision=1
  if [ -f "$descriptor" ]; then
    revision=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['revision'] + 1)" "$descriptor")
  fi

  # How long another network may serve a copy of our catalogues, and -- the
  # half that shows in a demo -- how often it must re-crawl to keep them. The
  # crawler refreshes at projectionTtl/3, floored at 30s, so 30 here means a
  # peer re-crawls us every 30 seconds.
  #
  # From the env so a demo can shorten it without editing anything. Absent
  # leaves oanfed's own default of an hour.
  local ttl_arg=()
  local ttl; ttl="$(sed -n 's/^FEDERATION_PROJECTION_TTL=//p' "$dir/.env" 2>/dev/null)"
  [ -n "$ttl" ] && ttl_arg=(--projection-ttl "$ttl")

  # The BASE registry url: oanfed appends /api/v1 to it itself.
  python3 "$HERE/oanfed.py" publish \
    --network-id "$network" \
    --registry-url "http://127.0.0.1:$(port_of "$network" REGISTRY_PORT)" \
    --key "$dir/keys/network.key" \
    --revision "$revision" \
    "${ttl_arg[@]}" \
    --out "$dir"
}

port_of() {
  python3 "$HERE/network-ports.py" ports "$1" | sed -n "s/^$2=//p"
}

# admit records OUR decision to deal with another network, in OUR registry.
#
#   network.sh admit <us> <them>
#
# --resolve is what makes a network id reachable from this machine: the id is a
# domain that only resolves on the shared bridge, so the request carries it as
# its Host and the connection goes to that network's published front-door port.
# The certificate is still checked against the id, so nothing is relaxed -- only
# where the connection goes.
cmd_admit() {
  local us="$1" them="$2"
  need_network "${them:-}"
  local env_file; env_file="$(network_dir "$us")/.env"
  local value; value() { sed -n "s/^$1=//p" "$env_file"; }

  python3 "$HERE/oanfed.py" admit \
    --network-id "$them" \
    --ca-file "$STATE/ca/ca.crt" \
    --resolve "127.0.0.1:$(port_of "$them" FRONT_DOOR_PORT)" \
    --registry-url "http://127.0.0.1:$(port_of "$us" REGISTRY_PORT)" \
    --keycloak-url "http://127.0.0.1:$(port_of "$us" KEYCLOAK_PORT)" \
    --keycloak-issuer "registry-keycloak:8080" \
    --realm "$(value KEYCLOAK_REALM)" \
    --client-id "$(value KEYCLOAK_CLIENT_ID)" \
    --username "$(value REGISTRY_ADMIN_USER)" \
    --password "$(value REGISTRY_ADMIN_PASSWORD)"
}

cmd_down()    { compose "$1" down; }
cmd_ps()      { compose "$1" ps; }
cmd_logs()    { shift; compose "$@"  logs -f --tail=100; }

# cmd_teardown destroys EVERY deployed network.
#
# A separate command rather than `destroy` with no argument: destroy already
# refuses an empty network id, and making that spelling mean "all of them"
# would turn the commonest typo into the most destructive outcome.
#
# It names what it is about to delete and waits, because the data it removes --
# every registry, every published catalog, both crawler databases -- cannot be
# rebuilt from this directory. Set TEARDOWN_YES=1 to skip the prompt in a
# script.
cmd_teardown() {
  local networks
  networks="$(python3 "$HERE/network-ports.py" list | awk '{print $1}')"

  if [ -z "$networks" ]; then
    echo "nothing deployed"
    return 0
  fi

  echo "this will DESTROY, with their data and ports:"
  echo "$networks" | sed 's/^/  /'

  if [ "${TEARDOWN_YES:-}" != "1" ]; then
    printf "type yes to continue: "
    local answer; read -r answer
    [ "$answer" = "yes" ] || { echo "nothing done"; return 1; }
  fi

  local network
  for network in $networks; do
    cmd_destroy "$network"
  done
}

cmd_destroy() {
  local network="$1"
  compose "$network" down -v
  python3 "$HERE/network-ports.py" release "$network"
  rm -rf "$(network_dir "$network")"
  echo "network: $network destroyed, and its ports released"
}

cmd_ls() { python3 "$HERE/network-ports.py" list; }

main() {
  local command="${1:-}"
  case "$command" in
    ls)      cmd_ls ;;
    up|setup|down|destroy|ps)
             need_network "${2:-}"; "cmd_$command" "$2" ;;
    # Its own line rather than joining the list above: that branch dispatches
    # to "cmd_$command", and a hyphen cannot appear in a shell function name.
    host-descriptor)
             need_network "${2:-}"; cmd_host_descriptor "$2" ;;
    teardown) cmd_teardown ;;
    admit)   need_network "${2:-}"; cmd_admit "$2" "${3:-}" ;;
    logs)    need_network "${2:-}"; compose "$2" logs -f --tail=100 "${3:-}" ;;
    *)       sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//' ;;
  esac
}

main "$@"
