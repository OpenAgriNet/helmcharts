#!/usr/bin/env bash
#
# Bring the stack up in the order it has to come up in, and take it back down.
#
#     bin/stack.sh up            the whole stack, in the order it has to start
#     bin/stack.sh up-core       the same minus the gateway and observability
#     bin/stack.sh down          stop everything, keep the data
#     bin/stack.sh destroy       stop everything, DELETE the data
#
# Everything here is also a make target: `make up`, `make down`.
#
# The order the steps run in, and why teardown names every profile, are
# docs/adr/0003-bringing-the-stack-up.md.

set -euo pipefail

# Every path in here is relative to the compose directory, and `docker compose`
# needs to find docker-compose.yml, so anchor to it rather than to $PWD. That
# makes `make -C quick-start up` work from anywhere.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Every optional profile, named explicitly: compose acts only on services whose
# profile is active, which is what makes `down` mean down (ADR-0003).
PROFILES=(--profile reverse-proxy --profile observability --profile multi-adapter)

# Deployment mode for `up`: NETWORK_ADAPTER_DEPLOYMENT_MODE from the shell, else .env, else
# single-adapter (ADR-0001).
# Other commands keep the running mode so depends_on never swaps network-adapter's config.

# A key's value in .env (quotes, inline comment and CR stripped); empty if absent.
dotenv_get() {
    local v
    v=$(grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-) || true
    v=${v%%#*}
    printf '%s' "$v" | tr -d '"'"'"' \t\r'
}

# Write a key back to .env, replacing the line or appending it. An answer given
# at a prompt belongs in the file, so .env never disagrees with what is running.
dotenv_set() {
    local key=$1 value=$2 tmp
    tmp=$(mktemp)
    awk -v k="$key" -v v="$value" '
        index($0, k "=") == 1 { print k "=" v; found = 1; next }
        { print }
        END { if (!found) print k "=" v }
    ' .env > "$tmp"
    cat "$tmp" > .env   # not mv: keeps the file's own mode and ownership
    rm -f "$tmp"
    info ".env: $key -> $value"
}

# What this network's containers are called. Empty for a single network, so the
# names are what they always were; a second network sets STACK_PREFIX in its own
# .env and every name it touches moves with it.
container() {
    printf '%s%s' "${STACK_PREFIX:-$(dotenv_get STACK_PREFIX)}" "$1"
}

# Running mode, from the config network-adapter mounts; empty if not running.
running_mode() {
    [ "$(docker inspect -f '{{.State.Running}}' "$(container network-adapter)" 2>/dev/null)" = true ] || return 0
    case "$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/config/adapter.yaml"}}{{.Source}}{{end}}{{end}}' "$(container network-adapter)" 2>/dev/null)" in
        */multi-adapter/*) echo multi-adapter ;;
        *) echo single-adapter ;;
    esac
}

# An environment variable is an answer already: configure-adapter does not ask
# for something it was told.
MODE_GIVEN="${NETWORK_ADAPTER_DEPLOYMENT_MODE:-${MODE:-}}"
MODE="${MODE_GIVEN:-$(dotenv_get NETWORK_ADAPTER_DEPLOYMENT_MODE)}"
MODE="${MODE:-single-adapter}"
case "$MODE" in single|multi) MODE="$MODE-adapter" ;; esac   # old values still accepted
RUNNING_MODE="$(running_mode)"

# compose mounts config/adapters/<mode>/network-adapter.yaml from this variable.
export NETWORK_ADAPTER_DEPLOYMENT_MODE="${RUNNING_MODE:-$MODE}"
check_mode() {
    case "$MODE" in single-adapter|multi-adapter) ;; *) die "NETWORK_ADAPTER_DEPLOYMENT_MODE must be single-adapter or multi-adapter, not '$MODE'" ;; esac
}

adapters_for() { # $1 mode
    case "$1" in
        single-adapter) echo network-adapter ;;
        multi-adapter)  echo provider-adapter network-adapter consumer-adapter ;;
    esac
}
# The rendered config files (config/adapters/<path>.yaml) a deployment mode mounts.
configs_for() { # $1 mode
    case "$1" in
        single-adapter) echo single-adapter/network-adapter ;;
        multi-adapter)  echo multi-adapter/provider-adapter multi-adapter/network-adapter multi-adapter/consumer-adapter ;;
    esac
}

# ------------------------------------------------------------------ output

# Steps are numbered in the output because the whole point of this script is
# that the order is load-bearing -- if it fails, you want to know at which one.
step()  { printf '\n\033[1;36m==> [%s/%s] %s\033[0m\n' "$1" "$2" "$3"; }
info()  { printf '    %s\n' "$1"; }
warn()  { printf '\033[1;33mwarning: %s\033[0m\n' "$1" >&2; }
die()   { printf '\033[1;31mstack: %s\033[0m\n' "$1" >&2; exit 1; }

# -------------------------------------------------------------- preflight

# Checked up front, not when first needed: step 1 blocks on a healthcheck, so a
# missing python3 would otherwise surface minutes later.
preflight() {
    [ -f .env ] || die ".env is missing -- cp .env.example .env, then change every credential in it"

    docker compose version >/dev/null 2>&1 \
        || die "docker compose (v2) is not available -- this needs the plugin, not docker-compose"

    command -v python3 >/dev/null 2>&1 \
        || die "python3 is not installed -- bin/setup.py needs it"

    python3 -c 'import cryptography' >/dev/null 2>&1 \
        || die "the python 'cryptography' package is missing -- pip install cryptography"

    # Grafana's alerting mount is ./config/grafana/alerting-<this value>, and
    # only alerting-true and alerting-false exist. Anything else (True, 1, yes)
    # is not an error to Docker: it creates an empty alerting-True/ and Grafana
    # silently loads no rules. The shell environment wins over .env, as it does
    # for compose.
    local alerting="${GRAFANA_ALERTING_ENABLED:-$(sed -n 's/^GRAFANA_ALERTING_ENABLED=//p' .env | tail -n 1 | tr -d "\"'")}"
    case "${alerting:-true}" in
        true|false) ;;
        *) die "GRAFANA_ALERTING_ENABLED must be exactly true or false, not '$alerting'" ;;
    esac

    mkdir -p .well-known  # before Docker can create it root-owned
}

# ------------------------------------------------------------------- up

# Containers only. No adapter starts here: its config does not exist until
# `make configure-adapter` renders it, and Docker would make a directory there.
up() {
    preflight
    up_registry_tier 3

    # NPM. This is the step that makes the VM reachable from the internet --
    # 0.0.0.0:80 and :443, deliberately not scoped, because Let's Encrypt
    # validates HTTP-01 from its own servers.
    step 2 3 "nginx-proxy-manager -- the public edge (80 and 443, all interfaces)"
    mkdir -p .well-known  # before Docker can create it root-owned
    docker compose --profile reverse-proxy up -d nginx-proxy-manager

    # ClickHouse, the collector, and Grafana. Heaviest thing here by a wide
    # margin: ClickHouse alone wants 2-4 GB, which is what takes this VM from
    # 8 GB to 16 GB.
    step 3 3 "clickhouse, otel-collector, grafana -- OTLP ingest, storage, UI"
    docker compose --profile observability up -d clickhouse otel-collector grafana

    done_banner
}

# Step 1 only. Nothing public, no ClickHouse -- the stack you can exercise.
up_core() {
    preflight
    up_registry_tier 1
    done_banner
}

# --- the shared steps, so `up` and `up-core` cannot drift apart -------------
#
# Each takes the step total so the numbering reads correctly in both.

# Naming these two also starts their Postgres: both are depends_on with
# condition: service_healthy, so compose blocks until they are ready.
#
# discovery belongs here, not with the adapters -- a failed discovery-postgres
# would otherwise surface as an adapter problem three steps later.
up_registry_tier() {
    step 1 "$1" "registry and discovery (also starts sunbird-registry-postgres, discovery-postgres)"
    docker compose up -d sunbird-registry-service discovery-service

    # `up -d` waits for a service's DEPENDENCIES, never the service itself, so
    # without this the next step races the registry's first boot.
    info "waiting for the registry to answer"
    local waited=0
    until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(container sunbird-registry-service)" 2>/dev/null)" = healthy ]; do
        [ "$waited" -ge 300 ] && die "the registry did not become healthy in 5 minutes -- docker logs $(container sunbird-registry-service)"
        sleep 5
        waited=$((waited + 5))
    done
}

# Started only by configure_adapter: before that there is no config to mount.
up_adapters() {
    # The mocks are NOT started: every capability points at a real upstream.
    # `docker compose up -d mock-imd mock-agmarknet` brings them up by hand.
    check_mode
    local f
    # `up` starts the requested mode, not the running one.
    NETWORK_ADAPTER_DEPLOYMENT_MODE="$MODE"
    step 2 2 "adapters, mode $MODE ($(adapters_for "$MODE"))"
    # A pre-merge stack's network-adapter container (service network-adapter-layer)
    # is unknown to compose and would clash by name.
    if [ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$(container network-adapter)" 2>/dev/null)" = network-adapter-layer ]; then
        docker rm -f network-adapter >/dev/null
    fi
    # Single runs network-adapter alone: remove the multi-only adapters.
    if [ "$MODE" = single-adapter ]; then
        docker compose --profile multi-adapter rm -sf provider-adapter consumer-adapter >/dev/null 2>&1 || true
    fi
    # Never let compose create a directory where a bind-mounted config belongs.
    for f in $(configs_for "$MODE"); do
        [ -f "config/adapters/$f.yaml" ] \
            || die "config/adapters/$f.yaml was not rendered (a directory there? rm -r it, then re-run)"
    done
    # shellcheck disable=SC2046
    docker compose --profile multi-adapter up -d --force-recreate $(adapters_for "$MODE")
    # NPM resolves adapter names when nginx loads and caches the addresses, so
    # a recreated or switched adapter 502s through it until it restarts.
    if [ "$(docker inspect -f '{{.State.Running}}' "$(container nginx-proxy-manager)" 2>/dev/null)" = true ]; then
        info "restarting nginx-proxy-manager so it re-resolves the adapter addresses"
        docker compose --profile reverse-proxy restart nginx-proxy-manager >/dev/null
    fi
}

done_banner() {
    printf '\n\033[1;32m==> containers are up\033[0m\n'
    docker compose "${PROFILES[@]}" ps
    cat <<'NEXT'

  The registry is empty and no adapter is running yet. In order:

      make keys
      make register-network
      make register-provider PROVIDER=mausamgram    (once per provider)
      make configure-adapter                        (renders, then starts them)

  Everything except NPM's 80 and 443 is bound to 127.0.0.1 on this host.
  From your laptop:

      ssh -L 81:127.0.0.1:81 -L 8080:127.0.0.1:8080 \
          -L 8081:127.0.0.1:8081 -L 8085:127.0.0.1:8085 -N you@the-vm

  If the gateway is running, its admin UI is on http://127.0.0.1:81 and still
  has its shipped login (admin@example.com / changeme) until you change it.
  That account can mint certificates and re-point every public route -- change
  it before creating anything.

NEXT
}

# ----------------------------------------------------------------- down

# Containers and networks go; named volumes stay. So the registry's Postgres
# data, discovery's data, and -- the one that would actually hurt -- nginx-proxy-manager-data,
# which is the ONLY copy of every proxy host and Let's Encrypt certificate,
# all survive. `up` after this is fast and lands where you left off.
down() {
    step 1 1 "stopping everything, keeping the data"
    docker compose "${PROFILES[@]}" down --remove-orphans
    info "volumes kept. 'make destroy' is the one that deletes them."
}

# -------------------------------------------------------------- destroy

# The asymmetry with `down` is deliberate: this is not recoverable, and one of
# the things it deletes was never in git to begin with.
destroy() {
    cat <<'WARN'
This deletes every named volume in the project:

  nginx-proxy-manager-data         every NPM proxy host and Let's Encrypt certificate. NPM
                   keeps its routing table in a SQLite database in this
                   volume and nowhere else -- there is no export, and the
                   admin account has no reset flow. If you have not backed
                   it up, the click-through starts over.
  sunbird-registry-postgres-data    the registry's Postgres: participants, keys, schemas.
  discovery-postgres-data   the discovery catalogue.
  clickhouse-data     collected telemetry.
  grafana-data        Grafana's own settings and dashboards (not telemetry).

keys/keys.json is NOT deleted, and should not be -- it is what lets setup.py
re-register the adapters under their existing identities on the next `up`.

WARN
    if [ "${FORCE:-}" != "1" ]; then
        [ -t 0 ] || die "not a terminal -- re-run as FORCE=1 make destroy if you mean it"
        read -r -p "Type 'destroy' to confirm: " reply
        [ "$reply" = "destroy" ] || die "aborted"
    fi

    step 1 1 "removing containers, networks and volumes"
    docker compose "${PROFILES[@]}" down -v --remove-orphans
}

# ------------------------------------------------------- optional tiers

# Separate targets rather than part of `up` because neither is needed to
# exercise the stack, and ClickHouse alone wants 2-4 GB.
reverse_proxy() {
    preflight
    step 1 1 "nginx-proxy-manager -- publishes 80 and 443 on ALL interfaces"
    mkdir -p .well-known  # before Docker can create it root-owned
    docker compose --profile reverse-proxy up -d nginx-proxy-manager
    cat <<'NEXT'

  The admin UI is on loopback, and ships with a live default login. Tunnel in
  and change it before creating anything:

      ssh -L 81:127.0.0.1:81 -N you@the-vm     then http://127.0.0.1:81

NEXT
}

observability() {
    preflight
    step 1 1 "clickhouse, otel-collector, grafana -- OTLP ingest, storage, UI"
    docker compose --profile observability up -d clickhouse otel-collector grafana
    info "UI on 127.0.0.1:8085. Grafana has its own login (GF_SECURITY_ADMIN_USER/PASSWORD) -- change it before exposing this, and keep it on loopback or behind an NPM Access List regardless."
}

# ------------------------------------------------------------------ pull

# `git pull`, with the one thing that otherwise stops it.
#
# config/reverse-proxy/npm-custom is bind-mounted into NPM, and NPM's s6 init chowns
# everything under /data/nginx on every start -- so those two files end up
# owned by a UID that is not you, and git cannot unlink them to update:
#
#   error: unable to unlink old '.../server_proxy.conf': Permission denied
#
# The mount cannot be :ro (that stops nginx from starting at all -- see the
# comment on the mount), and git does not track ownership, so there is nothing
# to fix once and for all. Taking the files back before pulling is the whole
# workaround, and it belongs in a target rather than in someone's memory.
pull() {
    step 1 2 "taking back ownership of config/reverse-proxy/npm-custom"
    if [ -n "$(find config/reverse-proxy/npm-custom ! -user "$(id -un)" -print -quit 2>/dev/null)" ]; then
        sudo chown -R "$(id -un):$(id -gn)" config/reverse-proxy/npm-custom
        info "done -- NPM had chowned them on its last start"
    else
        info "already yours, nothing to do"
    fi

    step 2 2 "git pull"
    git -C "$(git rev-parse --show-toplevel)" pull
    cat <<'NEXT'

  Then apply what came in:

      make up            new services, changed images or .env
      make restart       changed adapter or registry config
      make restart-edge  changed config/reverse-proxy/npm-custom

NEXT
}

# --------------------------------------------------------------- restart

# The services that hold OAN's own code and config, and nothing else.
#
# Deliberately excludes the two Postgres instances: those are state, they are
# slow to come back, and nothing you change in this repo alters their
# behaviour. Restarting them to pick up a config change is downtime that cannot
# have helped.
#
# It also excludes nginx-proxy-manager, whose routing table lives in a SQLite
# database rather than in anything a restart would re-read. `restart-edge` is
# the separate target for the one case that does need it.

# `restart`, not `up -d --force-recreate`. A restart keeps the container and
# therefore its address, so NPM's cached proxy_pass targets stay valid -- a
# recreate changes the address and leaves every proxy host 502ing until
# `restart-edge` runs. Same reason the compose file spells this out.
#
# What this picks up: the registry re-reads config/registry/schemas, and each
# adapter re-reads the config setup.py rendered for it. What it does not pick
# up is a changed image or a changed environment, both of which need the
# container recreated -- use `make up` for those.
restart_app() {
    [ -n "$RUNNING_MODE" ] && MODE=$RUNNING_MODE
    check_mode
    step 1 1 "restarting registry, discovery and the $MODE-mode adapters"
    info "both databases and the edge are left alone"
    # shellcheck disable=SC2046
    docker compose --profile multi-adapter restart sunbird-registry-service discovery-service $(adapters_for "$MODE")
    docker compose --profile multi-adapter ps --format 'table {{.Service}}\t{{.Status}}'
}

# ----------------------------------------------------------------- misc

# NPM writes a literal proxy_pass hostname per proxy host, which nginx resolves
# at reload and then caches. RECREATE an adapter -- not merely restart it, a
# restart keeps the address -- and NPM goes on proxying to an address nothing
# answers on. This is the fix, and it is worth having as a target because the
# symptom is a bare 502 that looks like the adapter is down.
restart_edge() {
    step 1 1 "restarting nginx-proxy-manager to re-resolve adapter addresses"
    docker compose --profile reverse-proxy restart nginx-proxy-manager
}

# ------------------------------------------------- filling the registry
#
# All four CREATE ONLY: an existing row is left alone, never overwritten. A
# wrong value is corrected with a PUT, so one provider at a time limits it.

# Generated once and reused. The private halves never leave keys/keys.json.
keys() {
    preflight
    step 1 1 "keys/keys.json"
    python3 bin/setup.py keys
}

# Our one entry, shared by all three adapters, and the key id read back out of
# it. Nothing can sign until this has run.
register_network() {
    preflight
    step 1 1 "our network entry"
    python3 bin/setup.py register-network
}

# One upstream, its binding and its schema. PROVIDER=mausamgram.
register_provider() {
    preflight
    [ -n "${PROVIDER:-}" ] \
        || die "which provider? e.g. make register-provider PROVIDER=mausamgram"
    step 1 1 "provider $PROVIDER"
    python3 bin/setup.py register-provider "$PROVIDER"
}

# Asked rather than passed as a flag. Skipped when given as an environment
# variable, or when there is no terminal -- CI keeps working off .env.

ask_mode() {
    if [ -n "$MODE_GIVEN" ] || [ ! -t 0 ]; then return 0; fi
    local answer marker_single="" marker_multi=""
    [ "$MODE" = single-adapter ] && marker_single="   [default]"
    [ "$MODE" = multi-adapter ]  && marker_multi="   [default]"
    printf '\nDeployment mode?\n'
    printf '  1) single-adapter   one container serving all three tiers%s\n' "$marker_single"
    printf '  2) multi-adapter    consumer, network and provider separately%s\n' "$marker_multi"
    read -rp '> ' answer
    case "$answer" in
        1|single|single-adapter) MODE=single-adapter ;;
        2|multi|multi-adapter)   MODE=multi-adapter ;;
        "")                      ;;   # the default stands
        *) die "answer 1 or 2, not '$answer'" ;;
    esac
    dotenv_set NETWORK_ADAPTER_DEPLOYMENT_MODE "$MODE"
}

# Whether this network crawls others. It still ANSWERS another network's crawl
# either way -- that is the network tier's job and this does not touch it.
ask_crawler() {
    if [ -n "${FEDERATION_CRAWL_PEERS:-}" ] || [ ! -t 0 ]; then return 0; fi
    local answer prompt crawl
    crawl=$(dotenv_get FEDERATION_CRAWL_PEERS)
    crawl=${crawl:-off}
    if [ "$crawl" = on ]; then prompt='[Y/n]'; else prompt='[y/N]'; fi
    read -rp "Enable the federation crawler? $prompt " answer
    case "$answer" in
        [Yy]*) crawl=on ;;
        [Nn]*) crawl=off ;;
        "")    ;;   # the default stands
        *) die "answer y or n, not '$answer'" ;;
    esac
    export FEDERATION_CRAWL_PEERS="$crawl"
    dotenv_set FEDERATION_CRAWL_PEERS "$crawl"
}

# Render from what the registry holds, then start the adapters. One command,
# because a rendered config nothing picked up is a change that did nothing.
configure_adapter() {
    preflight
    ask_mode
    ask_crawler
    check_mode
    step 1 2 "adapter configs, from the registry's bindings"
    python3 bin/setup.py configure-adapter
    up_adapters
    printf '\n\033[1;32m==> adapters are up\033[0m\n'
}

# Sign the federation documents into .well-known/ and admit another network.
# Each run of host-descriptor publishes the next revision.
#
# PEER, DIRECTION and PACKS come from the environment. RESOLVE and CA_FILE are
# for two networks on one machine, behind a local CA.
peer_access() {
    printf '%s' "${RESOLVE:+--resolve $RESOLVE} ${CA_FILE:+--ca-file $CA_FILE} ${SCHEME:+--scheme $SCHEME}"
}

need_peer() {
    [ -n "${PEER:-}" ] || die "which peer? e.g. make $1 PEER=net-b.oan.local"
}

need_direction() {
    case "${DIRECTION:-}" in
        source|requester) ;;
        *) die "DIRECTION must be source (we read from them) or requester (they read from us)" ;;
    esac
}

admit() {
    preflight
    need_peer admit
    need_direction
    # With none, oanfed lists what this direction can cover and refuses.
    #   PACKS=all   everything they publish today, or a comma-separated subset
    step 1 1 "bin/oanfed.py admit -- $DIRECTION, $PEER"
    # shellcheck disable=SC2046
    python3 bin/oanfed.py admit --network-id "$PEER" --direction "$DIRECTION" \
        --schema-packs "${PACKS:-}" --out . $(peer_access)
}

refresh_peer() {
    preflight
    need_peer refresh
    step 1 1 "bin/oanfed.py refresh -- $PEER"
    # shellcheck disable=SC2046
    python3 bin/oanfed.py refresh --network-id "$PEER" $(peer_access)
}

suspend_peer() {
    preflight
    need_peer suspend
    need_direction
    step 1 1 "bin/oanfed.py suspend -- $DIRECTION, $PEER"
    python3 bin/oanfed.py suspend --network-id "$PEER" --direction "$DIRECTION"
}

verify_peer() {
    preflight
    need_peer verify
    step 1 1 "bin/oanfed.py verify -- $PEER"
    # shellcheck disable=SC2046
    python3 bin/oanfed.py verify --network-id "$PEER" $(peer_access)
}

host_descriptor() {
    preflight
    step 1 3 "bin/oanfed.py publish -- the Network Descriptor, JWKS and schema-pack list"
    local revision=1
    if [ -f .well-known/openagrinet ]; then
        revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"] + 1)' \
                   .well-known/openagrinet)
    fi
    # TTL last: it may be empty.
    local values
    values=$(python3 -c 'import os, sys; sys.path.insert(0, "bin"); import setup
setup.load_dotenv()
print(setup.env("BECKN_NETWORK_ID"), setup.registry_url(),
      setup.env("REVERSE_PROXY_HTTPS_PORT", "443"), os.environ.get("FEDERATION_PROJECTION_TTL", ""))')
    read -r network_id registry https_port ttl <<<"$values"
    python3 bin/oanfed.py publish \
        --network-id "$network_id" --registry-url "$registry" \
        --keys keys/keys.json --revision "$revision" \
        ${ttl:+--projection-ttl "$ttl"} --out .

    step 2 3 "nginx-proxy-manager -- serves .well-known/ on every proxy host"
    local was_running
    was_running=$(docker inspect -f '{{.State.Running}}' "$(container nginx-proxy-manager)" 2>/dev/null || true)
    docker compose --profile reverse-proxy up -d nginx-proxy-manager
    if [ "$was_running" = true ]; then
        docker exec "$(container nginx-proxy-manager)" nginx -s reload >/dev/null 2>&1 \
            || warn "NPM did not reload; run make restart-edge if the check below fails"
    else
        info "NPM just started. Change its default login before anything else: make reverse-proxy says how."
    fi

    step 3 3 "checking https://$network_id/.well-known/openagrinet through NPM"
    # Checks routing only (-k); a peer checks the certificate.
    local served
    served=$(curl -fsSk --max-time 10 --resolve "$network_id:$https_port:127.0.0.1" \
             "https://$network_id:$https_port/.well-known/openagrinet" 2>/dev/null \
             | python3 -c 'import json,sys; print(json.load(sys.stdin)["revision"])' 2>/dev/null || true)
    if [ "$served" = "$revision" ]; then
        info "served at revision $revision -- peers can fetch it now"
    else
        warn "written, but NPM is not serving it for $network_id yet${served:+ (it serves revision $served)}"
        info "NPM needs a proxy host, with a certificate, for $network_id."
        info "Create it, then re-run make host-descriptor."
    fi
}

usage() {
    cat <<'USAGE'
bin/stack.sh <command>

  up             the containers: registry+discovery -> gateway (public, 80/443)
                 -> observability (wants 16 GB). No registry writes, no adapters.
  up-core        the registry and discovery only. Nothing public, no ClickHouse.
  down           stop everything, keep the data
  destroy        stop everything and DELETE every volume

  then, in order -- each is idempotent:
  keys               generate keys/keys.json
  register-network   publish our own entry and read its key id back
  register-provider  one upstream, its binding and its schema   PROVIDER=
  configure-adapter  render the adapter configs from the registry, start them
                     Asks for the deployment mode and whether to crawl other
                     networks, and writes both answers back to .env. Set
                     NETWORK_ADAPTER_DEPLOYMENT_MODE or FEDERATION_CRAWL_PEERS
                     to answer without being asked; with no terminal it does
                     not ask at all.

  host-descriptor  sign the descriptor, JWKS and schema packs into .well-known/
                   and serve them through nginx-proxy-manager (public, 80/443)
  admit            admit a peer in one direction   PEER= DIRECTION= PACKS=
                   PACKS is `all` or a comma-separated subset; omit it to be
                   shown what this direction can cover
  refresh          re-read an admitted peer's documents            PEER=
  suspend          withdraw one direction          PEER= DIRECTION=
  verify           fetch and check a peer's documents, changing nothing   PEER=
  reverse-proxy  start nginx-proxy-manager on its own (public, 80/443)
  observability  start clickhouse, otel-collector and grafana on their own
  pull           git pull, fixing the npm-custom ownership first
  restart        restart registry, discovery and the adapters only
  restart-edge   restart NPM after recreating an adapter (fixes a 502)
  ps             docker compose ps
  logs [service] docker compose logs -f

USAGE
}

# Every command takes no arguments except logs; an extra one is a mistake, not a mode.
[ $# -le 1 ] || [ "${1:-}" = logs ] || { usage; die "unexpected argument: $2"; }
case "${1:-}" in
    up)             up ;;
    up-core)        up_core ;;
    down)           down ;;
    destroy)        destroy ;;
    keys)           keys ;;
    register-network) register_network ;;
    register-provider) register_provider ;;
    configure-adapter) configure_adapter ;;
    host-descriptor) host_descriptor ;;
    admit)          admit ;;
    refresh)        refresh_peer ;;
    suspend)        suspend_peer ;;
    verify)         verify_peer ;;
    reverse-proxy)  reverse_proxy ;;
    observability)  observability ;;
    pull)           pull ;;
    restart)        restart_app ;;
    restart-edge)   restart_edge ;;
    ps)             docker compose "${PROFILES[@]}" ps ;;
    logs)           shift; docker compose "${PROFILES[@]}" logs -f "$@" ;;
    ""|-h|--help)   usage ;;
    *)              usage; die "unknown command: $1" ;;
esac
