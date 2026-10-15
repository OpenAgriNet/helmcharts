#!/usr/bin/env bash
# Give each network a TLS certificate for its own name.
#
# The contract says a network is reached over https, and the registry's
# Participant schema pins baseUrl to ^https://. Serving plain HTTP locally would
# mean either weakening that schema or storing a URL the stack cannot use -- both
# of which hide the thing a POC is supposed to exercise.
#
# So: one local CA, and a certificate per network signed by it. Peers verify
# against the CA, which is real certificate verification. What a laptop still
# cannot do is prove it OWNS the domain -- that is the one property this setup
# does not and cannot demonstrate, and it is why the CA is trusted explicitly by
# path rather than installed into any trust store.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Everything written here is private key material, so it all lands under
# networks/, which is gitignored as a whole.
NETWORKS="$(dirname "$HERE")/networks"
CA_DIR="$NETWORKS/ca"
DAYS=825

mkdir -p "$CA_DIR"

if [ ! -f "$CA_DIR/ca.crt" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$CA_DIR/ca.key" -out "$CA_DIR/ca.crt" -days "$DAYS" \
    -subj "/CN=OAN federation local CA" 2>/dev/null
  chmod 600 "$CA_DIR/ca.key"
  echo "created the local CA at $CA_DIR/ca.crt"
fi

for network in "$@"; do
  dir="$NETWORKS/$network/tls"
  mkdir -p "$dir"

  # The name must be in a subjectAltName: common name alone has not been
  # accepted by anything for years, and a cert that fails only at verification
  # time is worse than one that fails here.
  openssl req -newkey rsa:2048 -nodes \
    -keyout "$dir/server.key" -out "$dir/server.csr" \
    -subj "/CN=$network" 2>/dev/null

  # The upstream names as well as the network's own. This network's mock
  # providers are reached at https://<provider>.<network>, because the registry
  # pins a provider's baseUrl to ^https:// just as it does a network's -- so the
  # front door has to answer for them, and that means holding their names.
  san="DNS:$network"
  for upstream in mausamgram agmarknet vistaar pocra; do
    san="$san,DNS:$upstream.$network"
  done

  openssl x509 -req -in "$dir/server.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -out "$dir/server.crt" -days "$DAYS" \
    -extfile <(printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\n' "$san") 2>/dev/null

  rm -f "$dir/server.csr"
  chmod 600 "$dir/server.key"

  # A copy of the CA beside this network's own material, so the compose file
  # mounts one directory rather than reaching across to a shared one. The
  # adapter trusts it by path, which is what lets an outbound call verify
  # another network's certificate with no code that knows about local CAs.
  cp "$CA_DIR/ca.crt" "$dir/ca.crt"
  echo "issued a certificate for $network"
done

echo
echo "Other networks verify against $CA_DIR/ca.crt. Pass it with --ca-file."
