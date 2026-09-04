#!/usr/bin/env bash
#
# Bring the stack up in the order it has to come up in, and take it back down.
#
#     bin/stack.sh up            the whole stack, in the order it has to start
#     bin/stack.sh up-core       the same minus the gateway and hyperdx
#     bin/stack.sh down          stop everything, keep the data
#     bin/stack.sh destroy       stop everything, DELETE the data
#
# Everything here is also a make target: `make up`, `make down`. The Makefile is
# the front door; this file is where the reasoning lives.
#
# ------------------------------------------------------------ why a script
#
# The steps in `up` are not interchangeable and the failure mode of getting
# them wrong is not obvious. An adapter's config is a bind-mounted
# FILE that setup.py renders. Docker creates a DIRECTORY at any bind-mount
# source that does not exist yet -- so starting an adapter before step 2 both
# wedges that container on a directory it cannot parse AND leaves a directory
# sitting where step 2 needs to write a file. Recovering means `rm -rf`ing
# paths under config/adapters/ that look like they should be there.
#
# So the ordering is worth encoding once rather than remembering three times.

set -euo pipefail

# Every path in here is relative to the compose directory, and `docker compose`
# needs to find docker-compose.yml, so anchor to it rather than to $PWD. That
# makes `make -C docker-deployment up` work from anywhere.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Both optional profiles, named explicitly. This matters for `down`: compose
# only acts on services whose profile is active, so a plain `docker compose
# down` leaves the gateway and hyperdx containers running and then reports
# success. Naming them on teardown is what makes "down" mean down.
PROFILES=(--profile gateway --profile observability)

# ------------------------------------------------------------------ output

# Steps are numbered in the output because the whole point of this script is
# that the order is load-bearing -- if it fails, you want to know at which one.
step()  { printf '\n\033[1;36m==> [%s/%s] %s\033[0m\n' "$1" "$2" "$3"; }
info()  { printf '    %s\n' "$1"; }
warn()  { printf '\033[1;33mwarning: %s\033[0m\n' "$1" >&2; }
die()   { printf '\033[1;31mstack: %s\033[0m\n' "$1" >&2; exit 1; }

# -------------------------------------------------------------- preflight

# Checked before anything starts, not when it is first needed. Step 1 blocks on
# Keycloak's healthcheck, which is 30 retries at 10s -- so a missing python3 or
# an unimportable `cryptography` would surface several minutes in, after a wait
# that had nothing to do with the problem. These take a millisecond each.
preflight() {
    [ -f .env ] || die ".env is missing -- cp .env.example .env, then change every credential in it"

    docker compose version >/dev/null 2>&1 \
        || die "docker compose (v2) is not available -- this needs the plugin, not docker-compose"

    command -v python3 >/dev/null 2>&1 \
        || die "python3 is not installed -- bin/setup.py needs it"

    python3 -c 'import cryptography' >/dev/null 2>&1 \
        || die "the python 'cryptography' package is missing -- pip install cryptography"
}

# ------------------------------------------------------------------- up

# The full stack, in the order the compose file's own header documents. Five
# steps rather than three: the gateway and hyperdx are behind profiles, which
# means they are opt-in for compose, but "opt-in" and "not part of bringing the
# stack up" are different claims and only the first one is true.
#
# `up-core` below is the same thing minus steps 4 and 5, for when you want
# neither a public port nor ClickHouse's memory.
up() {
    preflight
    up_registry_tier 5
    up_setup         5
    up_adapters      5

    # NPM. This is the step that makes the VM reachable from the internet --
    # 0.0.0.0:80 and :443, deliberately not scoped, because Let's Encrypt
    # validates HTTP-01 from its own servers.
    step 4 5 "nginx-proxy-manager -- the public edge (80 and 443, all interfaces)"
    docker compose --profile gateway up -d nginx-proxy-manager

    # ClickStack. Heaviest thing here by a wide margin: ClickHouse alone wants
    # 2-4 GB, which is what takes this VM from 8 GB to 16 GB.
    step 5 5 "hyperdx -- ClickStack (OTLP ingest, ClickHouse, UI)"
    docker compose --profile observability up -d hyperdx

    done_banner
}

# Steps 1-3 only. Nothing publishes on a routable interface and nothing needs
# 16 GB -- this is the stack you can actually exercise, which is the reason the
# profiles exist in the first place.
up_core() {
    preflight
    up_registry_tier 3
    up_setup         3
    up_adapters      3
    done_banner
}

# --- the shared steps, so `up` and `up-core` cannot drift apart -------------
#
# Each takes the step total so the numbering reads correctly in both.

# Naming registry and discovery also starts registry-db, keycloak and
# discovery-db: all are depends_on with condition: service_healthy, so compose
# blocks here until they pass their healthchecks rather than racing ahead.
#
# discovery belongs in this step and not in step 3. It would be dragged in
# anyway by network-adapter's depends_on, but then a discovery-db that failed
# to come up would surface as an adapter problem three steps later.
#
# No --wait. It would only add a second wait on the registry's own healthcheck,
# and setup.py already polls with an error message that says what to check.
up_registry_tier() {
    step 1 "$1" "registry and discovery (also starts registry-db, keycloak, discovery-db)"
    info "keycloak's healthcheck allows up to 5 minutes on a cold volume"
    docker compose up -d registry discovery
}

# Generates the adapter keypairs, registers the three adapter identities, and
# renders config/adapters/{provider,network,exp}.yaml from the .tmpl files
# beside them. Safe to re-run: keys come from keys/keys.json once it exists,
# and participants already registered are left alone.
up_setup() {
    step 2 "$1" "bin/setup.py -- keys, five registry participants, adapter configs"
    python3 bin/setup.py
}

# Only now do the bind-mounted config files exist.
up_adapters() {
    # The mocks are named here rather than left to provider-adapter's
    # depends_on, so a failure to pull one is reported as its own step instead
    # of as an adapter that will not start.
    step 3 "$1" "mock upstreams and adapters (provider, network, exp)"
    docker compose up -d mockimd mockagmarknet
    docker compose up -d provider-adapter network-adapter exp-adapter
}

done_banner() {
    printf '\n\033[1;32m==> stack is up\033[0m\n'
    docker compose "${PROFILES[@]}" ps
    cat <<'NEXT'

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
# data, discovery's data, and -- the one that would actually hurt -- npm-data,
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

  npm-data         every NPM proxy host and Let's Encrypt certificate. NPM
                   keeps its routing table in a SQLite database in this
                   volume and nowhere else -- there is no export, and the
                   admin account has no reset flow. If you have not backed
                   it up, the click-through starts over.
  registry-data    the registry's Postgres: participants, keys, schemas.
  discovery-data   the discovery catalogue.
  hyperdx-data     collected telemetry.

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
# exercise the stack, and hyperdx (ClickHouse) alone wants 2-4 GB.
gateway() {
    preflight
    step 1 1 "nginx-proxy-manager -- publishes 80 and 443 on ALL interfaces"
    docker compose --profile gateway up -d nginx-proxy-manager
    cat <<'NEXT'

  The admin UI is on loopback, and ships with a live default login. Tunnel in
  and change it before creating anything:

      ssh -L 81:127.0.0.1:81 -N you@the-vm     then http://127.0.0.1:81

NEXT
}

observability() {
    preflight
    step 1 1 "hyperdx -- ClickStack (OTLP ingest, ClickHouse, UI)"
    docker compose --profile observability up -d hyperdx
    info "UI on 127.0.0.1:8085. There is no login in front of it -- keep it on loopback."
}

# --------------------------------------------------------------- restart

# The services that hold OAN's own code and config, and nothing else.
#
# Deliberately excludes the two Postgres instances and keycloak: those are
# state, they are slow to come back, and nothing you change in this repo
# alters their behaviour -- keycloak reads its realm from a database that was
# seeded on first boot, not from a file you can edit. Restarting them to pick
# up a config change is a minute of downtime that cannot have helped.
#
# It also excludes nginx-proxy-manager, whose routing table lives in a SQLite
# database rather than in anything a restart would re-read. `restart-edge` is
# the separate target for the one case that does need it.
APP_SERVICES=(registry discovery provider-adapter network-adapter exp-adapter)

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
    step 1 1 "restarting registry, discovery and the three adapters"
    info "keycloak, both databases and the edge are left alone"
    docker compose restart "${APP_SERVICES[@]}"
    docker compose ps --format 'table {{.Service}}\t{{.Status}}'
}

# ----------------------------------------------------------------- misc

# NPM writes a literal proxy_pass hostname per proxy host, which nginx resolves
# at reload and then caches. RECREATE an adapter -- not merely restart it, a
# restart keeps the address -- and NPM goes on proxying to an address nothing
# answers on. This is the fix, and it is worth having as a target because the
# symptom is a bare 502 that looks like the adapter is down.
restart_edge() {
    step 1 1 "restarting nginx-proxy-manager to re-resolve adapter addresses"
    docker compose --profile gateway restart nginx-proxy-manager
}

# Just step 2. Re-run it after editing a .tmpl, or to re-render configs that
# were deleted. It is idempotent, so this is always safe.
setup() {
    preflight
    step 1 1 "bin/setup.py"
    python3 bin/setup.py
}

usage() {
    cat <<'USAGE'
bin/stack.sh <command>

  up             the whole stack: registry+discovery -> setup.py -> adapters
                 -> gateway (public, 80/443) -> hyperdx (wants 16 GB)
  up-core        steps 1-3 only. Nothing public, no ClickHouse.
  down           stop everything, keep the data
  destroy        stop everything and DELETE every volume
  setup          re-run bin/setup.py only
  gateway        start nginx-proxy-manager on its own (public, 80/443)
  observability  start hyperdx on its own
  restart        restart registry, discovery and the adapters only
  restart-edge   restart NPM after recreating an adapter (fixes a 502)
  ps             docker compose ps
  logs [service] docker compose logs -f

USAGE
}

case "${1:-}" in
    up)             up ;;
    up-core)        up_core ;;
    down)           down ;;
    destroy)        destroy ;;
    setup)          setup ;;
    gateway)        gateway ;;
    observability)  observability ;;
    restart)        restart_app ;;
    restart-edge)   restart_edge ;;
    ps)             docker compose "${PROFILES[@]}" ps ;;
    logs)           shift; docker compose "${PROFILES[@]}" logs -f "$@" ;;
    ""|-h|--help)   usage ;;
    *)              usage; die "unknown command: $1" ;;
esac
