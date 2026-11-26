#!/usr/bin/env bash
# Publish the MandiPrice state catalogs that are not inlined in the Postman
# collection.
#
# The documents beside this script carry {{variables}}, exactly as the
# collection's requests do, so one set of files serves every environment.
# This substitutes them and POSTs the result.
#
#   NETWORK_ID=oan.dev DISCOVERY_URL=http://127.0.0.1:8090 ./publish-states.sh
#   NETWORK_ID=oan.dev DISCOVERY_URL=http://127.0.0.1:8090 ./publish-states.sh MH GJ
#
# No arguments publishes every state. Named states publish only those, and a
# state split across several catalogs sends ALL of them: under FULL each
# replaces only its own catalogId, so sending one and not the other would
# leave half the state stale.
set -euo pipefail
cd "$(dirname "$0")"

: "${NETWORK_ID:?set NETWORK_ID -- a catalog published to the wrong network is invisible}"
: "${DISCOVERY_URL:?set DISCOVERY_URL -- discovery base address, /publish is appended}"
SENDER_ID="${SENDER_ID:-provider.oan.dev}"
RECEIVER_ID="${RECEIVER_ID:-discovery.oan.dev}"

files=()
if [ "$#" -gt 0 ]; then
  for code in "$@"; do
    # The second glob catches a split state's extra catalogs (MH-2).
    for f in "catalog-$code.json" "catalog-$code"-*.json; do
      [ -e "$f" ] && files+=("$f")
    done
  done
  if [ "${#files[@]}" -eq 0 ]; then
    echo "no documents matched: $*" >&2
    exit 1
  fi
else
  files=(catalog-*.json)
fi

ok=0
bad=0
for file in "${files[@]}"; do
  body=$(sed -e "s|{{networkId}}|$NETWORK_ID|g" -e "s|{{senderId}}|$SENDER_ID|g" -e "s|{{receiverId}}|$RECEIVER_ID|g" -e "s|{{transactionId}}|$(uuidgen)|g" -e "s|{{messageId}}|$(uuidgen)|g" -e "s|{{timestamp}}|$(date -u +%Y-%m-%dT%H:%M:%SZ)|g" "$file")

  answer=$(printf '%s' "$body" | curl -sS -X POST "$DISCOVERY_URL/publish" -H 'Content-Type: application/json' --data-binary @-)

  # ACCEPTED only. A PARTIAL means the catalog indexed with pieces missing,
  # which is a failure however healthy the 200 looks.
  if printf '%s' "$answer" | grep -q '"status":"ACCEPTED"'; then
    printf '  %-24s ACCEPTED\n' "$file"
    ok=$((ok + 1))
  else
    printf '  %-24s FAILED: %s\n' "$file" "$(printf '%s' "$answer" | head -c 200)" >&2
    bad=$((bad + 1))
  fi
done

echo
echo "published $ok, failed $bad"
[ "$bad" -eq 0 ]
