#!/usr/bin/env bash
# Give this network a TLS certificate for its own name.
#
# The contract says a network is reached over https, and the registry pins
# baseUrl to ^https://. Serving plain http locally would mean either weakening
# that schema or storing a URL the stack cannot call.
#
# One CA, kept outside the repo so every network on this machine shares it, and
# a certificate per network signed by it. Another network verifies against the
# CA, which is real verification. What a laptop cannot prove is that it OWNS the
# name -- which is why the CA is trusted by path and installed into no store.
set -euo pipefail

CA_DIR="${OAN_CA_DIR:-$HOME/.oan/federation-ca}"
DAYS=825

network="${1:-}"
[ -n "$network" ] || { echo "usage: network-certs.sh <network-id>" >&2; exit 1; }

mkdir -p "$CA_DIR"

if [ ! -f "$CA_DIR/ca.crt" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.crt" -days "$DAYS" \
        -subj "/CN=OAN federation local CA" 2>/dev/null
    chmod 600 "$CA_DIR/ca.key"
    echo "    created the shared local CA at $CA_DIR/ca.crt"
else
    echo "    reusing the shared local CA at $CA_DIR/ca.crt"
fi

mkdir -p tls

# The name must be in a subjectAltName: a common name alone has not been
# accepted for years, and failing at verification time is worse than failing here.
san="DNS:$network"
for upstream in mausamgram agmarknet vistaar pocra; do
    san="$san,DNS:$upstream.$network"
done

openssl req -newkey rsa:2048 -nodes \
    -keyout tls/server.key -out tls/server.csr -subj "/CN=$network" 2>/dev/null

openssl x509 -req -in tls/server.csr \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out tls/server.crt -days "$DAYS" \
    -extfile <(printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\n' "$san") 2>/dev/null

rm -f tls/server.csr
chmod 600 tls/server.key

# Beside this network's own material, so compose mounts one directory and the
# adapter trusts the CA by path with no code that knows about local CAs.
cp "$CA_DIR/ca.crt" tls/ca.crt
echo "    issued a certificate for $network, valid $DAYS days"
