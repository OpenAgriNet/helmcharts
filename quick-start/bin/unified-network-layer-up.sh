#!/usr/bin/env bash
# Build the adapter image with in-process (inproc://) routing and run the
# unified network layer on it -- ONE adapter for the consumer, network and
# provider tiers (the unified single adapter) -- then prove it works.
#
#   bin/unified-network-layer-up.sh              build image, start stack, smoke test
#   bin/unified-network-layer-up.sh --no-build   reuse the existing image
#   HTTP=1 bin/unified-network-layer-up.sh       tier hops over http://localhost instead
#
# Env:
#   ADAPTER_SRC  network-adapter checkout to build from (default ../../network-adapter)
#   IMAGE        image tag to build and run            (default: UNIFIED_NETWORK_LAYER_ADAPTER_IMAGE
#                from .env, else network-adapter:inproc)
#
# Uses docker-compose.unified-network-layer.yml (and docker-compose.arm64.yml
# when present). Leaves the unified network layer RUNNING.
# Stop it: make down-unified-network-layer
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0
ADAPTER_SRC="${ADAPTER_SRC:-$ROOT/../../network-adapter}"
ENV_IMAGE="$(grep -E '^UNIFIED_NETWORK_LAYER_ADAPTER_IMAGE=' .env 2>/dev/null | cut -d= -f2-)"
IMAGE="${IMAGE:-${ENV_IMAGE:-network-adapter:inproc}}"

COMPOSE=(-f docker-compose.unified-network-layer.yml)
[ -f docker-compose.arm64.yml ] && COMPOSE+=(-f docker-compose.arm64.yml)
# HTTP=1: tier hops over http://localhost instead of inproc:// (any image).
[ "${HTTP:-}" = 1 ] && COMPOSE+=(-f docker-compose.unified-network-layer-http.yml)
PORT="$(grep -E '^UNIFIED_NETWORK_LAYER_ADAPTER_PORT=' .env 2>/dev/null | cut -d= -f2)"; PORT="${PORT:-9210}"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f .env ] || die ".env missing: cp .env.example .env and fill it in"

# --- 1. build ---------------------------------------------------------------
if [ "$BUILD" = 1 ]; then
  step "1/5 build $IMAGE from $ADAPTER_SRC"
  [ -f "$ADAPTER_SRC/core/module/handler/inproc.go" ] \
    || die "$ADAPTER_SRC has no core/module/handler/inproc.go: check out a branch with in-process routing"
  BRANCH="$(git -C "$ADAPTER_SRC" rev-parse --abbrev-ref HEAD)"
  COMMIT="$(git -C "$ADAPTER_SRC" rev-parse --short HEAD)"
  DIRTY="clean"; [ -n "$(git -C "$ADAPTER_SRC" status --porcelain)" ] && DIRTY="dirty"
  echo "   source: $BRANCH @ $COMMIT ($DIRTY)"
  docker build --progress=plain -f "$ADAPTER_SRC/Dockerfile.adapter-with-plugins" \
    --build-arg ONIX_VERSION="$BRANCH" --build-arg GIT_COMMIT="$COMMIT" \
    --build-arg GIT_TREE_STATE="$DIRTY" --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    -t "$IMAGE" -t "network-adapter:inproc-$COMMIT" "$ADAPTER_SRC"
else
  step "1/5 reuse $IMAGE"
  docker image inspect "$IMAGE" >/dev/null 2>&1 || die "$IMAGE not found; run without --no-build"
fi

# --- 2. infrastructure ------------------------------------------------------
step "2/5 registry, discovery, mocks"
docker compose "${COMPOSE[@]}" up -d sunbird-registry-service discovery-service mock-imd mock-agmarknet

# --- 3. config --------------------------------------------------------------
step "3/5 keys, registry entries, rendered config (bin/setup.py)"
python3 bin/setup.py >/dev/null
echo "   config/adapters/unified-network-layer/unified-network-layer.yaml rendered"

# --- 4. adapter on the inproc image -----------------------------------------
step "4/5 unified network layer on $IMAGE (inproc routing)"
# The multi-adapter containers share this project; only one topology at a time.
docker compose -f docker-compose.yml stop provider-adapter network-adapter consumer-adapter >/dev/null 2>&1 || true
UNIFIED_NETWORK_LAYER_ADAPTER_IMAGE="$IMAGE" docker compose "${COMPOSE[@]}" up -d --force-recreate network-layer-adapter
for i in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break
  [ "$i" = 60 ] && { docker logs --tail 30 network-layer-adapter; die "adapter not healthy after 60s"; }
  sleep 1
done
echo "   image: $(docker inspect network-layer-adapter --format '{{.Config.Image}}')"
echo "   health: $(curl -s "http://127.0.0.1:$PORT/health")"

# --- 5. smoke test ----------------------------------------------------------
step "5/5 publish, discover, select through the unified network layer"
python3 bin/poc-flow.py --mode unified-network-layer --n 3
echo
echo "   in-process hops seen in the adapter log:"
docker logs --since 2m network-layer-adapter 2>&1 | grep -o 'Forwarding request to URL: [^"]*' | sort | uniq -c | sed 's/^/   /'

printf '\n\033[1;32mUnified network layer running on %s (inproc routing). Port 127.0.0.1:%s\033[0m\n' "$IMAGE" "$PORT"
echo "Demo: bin/demo-unified-network-layer.sh   Postman: api-collection/OpenAgriNet.unified-network-layer.collection.json"
