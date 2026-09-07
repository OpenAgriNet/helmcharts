#!/usr/bin/env bash
# Download the published schema packs into config/schemas.
#
# These are what the provider adapter's EXTENDED schema validation checks
# resourceAttributes against. Base validation covers the Beckn envelope and
# treats resourceAttributes as a free-form object; extended validation resolves
# each resource's @type to one of these documents and validates the object
# against it, which is what makes a wrong unit or a missing required attribute a
# rejected payload rather than something a provider discovers later.
#
# The layout below mirrors the published tree because that is what the adapter
# preloads: it walks localSchemaPath at STARTUP, keys every *.yaml by
# <TypeName>/attributes.yaml with the version segment dropped, and then looks up
# an object's @type -- the part after the colon -- directly. So no payload costs
# a network call, and the container needs no egress to validate.
#
# Not committed, and fetched rather than vendored, for the same reason the
# mappings are not vendored: a copy here would drift from what the network
# publishes, and what this stack validates against has to be what consumers
# actually read.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ -f .env ] && { set -a; . ./.env; set +a; }

: "${SCHEMA_PACKS_URL:?set SCHEMA_PACKS_URL in .env (see .env.example)}"
: "${BECKN_SCHEMA_URL:?set BECKN_SCHEMA_URL in .env (see .env.example)}"

DEST=config/schemas

# The OAN capability packs, each at v0.1. Listed rather than discovered: the set
# changes rarely, and an explicit list fails loudly when a name moves instead of
# silently fetching fewer schemas than the adapter needs.
OAN_PACKS=(
    AgricultureResource     # the base every capability below inherits from
    AgricultureFacility
    KnowledgeAdvisory
    KnowledgeResource
    MandiPrice
    MarketIntelligence
    WeatherAdvisory
    WeatherObservation
)

# The packs $ref these, so they have to be in the same directory. Without them
# the packs load but their references dangle, and a payload fails on a resolver
# error rather than on anything wrong with the payload. Versions are the ones
# the packs actually name -- Descriptor is v2.1 where the rest are v2.0.
BECKN_SCHEMAS=(
    "Address/v2.0"
    "Contact/v2.0"
    "Descriptor/v2.1"
    "GeoJSONGeometry/v2.0"
    "Location/v2.0"
)

fetch() {
    local url="$1" out="$2"
    mkdir -p "$(dirname "$out")"
    # -f so an HTML 404 page is an error rather than a schema that fails to
    # parse later; -L because schema.beckn.io redirects.
    if ! curl -fsSL "$url" -o "$out"; then
        echo "failed: $url" >&2
        return 1
    fi
    printf '  %-56s %6s bytes\n' "$out" "$(wc -c <"$out")"
}

echo "Fetching schema packs into $DEST/"
rm -rf "$DEST"

for pack in "${OAN_PACKS[@]}"; do
    fetch "${SCHEMA_PACKS_URL}/${pack}/v0.1/attributes.yaml" \
          "${DEST}/${pack}/v0.1/attributes.yaml"
done

for ref in "${BECKN_SCHEMAS[@]}"; do
    fetch "${BECKN_SCHEMA_URL}/${ref}/attributes.yaml" \
          "${DEST}/${ref}/attributes.yaml"
done

count=$(find "$DEST" -name '*.yaml' | wc -l)
echo "Fetched $count schemas."

# The adapter refuses to start on a missing directory, which is the behaviour to
# want -- the alternative is accepting unvalidated payloads because a mount was
# forgotten. An empty one only warns, so check here where the message is useful.
if [ "$count" -eq 0 ]; then
    echo "no schemas fetched -- the provider adapter will reject every payload" >&2
    exit 1
fi
