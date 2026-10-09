#!/usr/bin/env bash
# Start the unified network layer -- ONE adapter for the consumer, network and
# provider tiers (the unified single adapter), tier hops over loopback HTTP --
# then prove it works.
#
#   bin/unified-network-layer-up.sh
#
# Image: ADAPTER_IMAGE from .env (the same as the multi-adapter stack). Port 9200.
# Uses docker-compose.unified-network-layer.yml (and docker-compose.arm64.yml
# when present). Leaves the unified network layer RUNNING.
# Stop it: make down-unified-network-layer
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(-f docker-compose.unified-network-layer.yml)
[ -f docker-compose.arm64.yml ] && COMPOSE+=(-f docker-compose.arm64.yml)
envv() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
PORT=9200
IMAGE="$(envv ADAPTER_IMAGE)"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f .env ] || die ".env missing: cp .env.example .env and fill it in"
[ -n "$IMAGE" ] || die "no image: set ADAPTER_IMAGE in .env"
command -v python3 >/dev/null 2>&1 || die "python3 is not installed -- bin/setup.py needs it"
python3 -c 'import cryptography' >/dev/null 2>&1 \
    || die "the python 'cryptography' package is missing -- pip install cryptography"
python3 -c 'import yaml' >/dev/null 2>&1 \
    || die "the python 'yaml' package (PyYAML) is missing -- pip install pyyaml, or apt install python3-yaml"

# --- 1. infrastructure ------------------------------------------------------
step "1/4 registry, discovery, mocks (the mocks are for this script's local smoke test; make up-unified-network-layer does not start them)"
docker compose "${COMPOSE[@]}" up -d sunbird-registry-service discovery-service mock-imd mock-agmarknet

# --- 2. config --------------------------------------------------------------
step "2/4 keys, registry entries, rendered config (bin/setup.py)"
UNIFIED_NETWORK_LAYER=1 python3 bin/setup.py >/dev/null
CFG=config/adapters/unified-network-layer/unified-network-layer.yaml
[ -f "$CFG" ] || die "$CFG was not rendered (a directory there? rm -r it, then re-run)"
echo "   $CFG rendered"

# --- 3. adapter -------------------------------------------------------------
step "3/4 network-layer-adapter on $IMAGE (loopback tier hops)"
# The multi-adapter containers share this project; only one topology at a time.
docker compose -f docker-compose.yml stop provider-adapter network-adapter consumer-adapter >/dev/null 2>&1 || true
docker compose "${COMPOSE[@]}" up -d --force-recreate network-layer-adapter
for i in $(seq 1 60); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break
  [ "$i" = 60 ] && { docker logs --tail 30 network-layer-adapter; die "adapter not healthy after 60s"; }
  sleep 1
done
echo "   image: $(docker inspect network-layer-adapter --format '{{.Config.Image}}')"
echo "   health: $(curl -s "http://127.0.0.1:$PORT/health")"

# --- 4. smoke test ----------------------------------------------------------
step "4/4 publish, discover, select through the unified network layer"
ROOT="$ROOT" PORT="$PORT" python3 - <<'PY'
import json, os, pathlib, sys, urllib.error, urllib.request, uuid
root = pathlib.Path(os.environ["ROOT"]); port = os.environ["PORT"]
env = {}
for line in (root / ".env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip()
steps = (("publish", "provider/publish"), ("discover", "consumer/discover"), ("select", "consumer/select"))
rc = 0
for step, path in steps:
    f = next((root / "capability-examples" / "WeatherObservation" / step).glob("*.json"))
    text = f.read_text()
    for old, key in (("provider.oan.dev", "PROVIDER_SUBSCRIBER_ID"),
                     ("consumer.oan.dev", "CONSUMER_SUBSCRIBER_ID"),
                     ("network.oan.dev", "NETWORK_SUBSCRIBER_ID")):
        text = text.replace(old, env[key])
    text = text.replace('"mausamgram"', json.dumps(env["MAUSAMGRAM_PARTICIPANT_ID"]))
    body = json.loads(text)
    body["context"]["transactionId"], body["context"]["messageId"] = str(uuid.uuid4()), str(uuid.uuid4())
    req = urllib.request.Request(f"http://127.0.0.1:{port}/{path}", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            status, data = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, data = e.code, e.read()
    print(f"   {step:9} /{path:18} HTTP {status}")
    if status != 200:
        rc = 1
        print("   body:", data[:300].decode(errors="replace"))
sys.exit(rc)
PY
echo
echo "   tier hops seen in the adapter log:"
docker logs --since 2m network-layer-adapter 2>&1 | grep -o 'Forwarding request to URL: [^"]*' | sort | uniq -c | sed 's/^/   /'

printf '\n\033[1;32mUnified network layer running on %s. Port 127.0.0.1:%s\033[0m\n' "$IMAGE" "$PORT"
