#!/usr/bin/env python3
"""oanfed -- the operator's tool for federation.

A network publishes exactly two files, at locations derived from its own domain:

    https://{networkId}/.well-known/openagrinet   the signed Network Descriptor
    https://{networkId}/.well-known/jwks.json     the keys that verify it

These are publication points, not APIs. Serving a static file and generating the
same JSON are equally valid, and nothing here assumes which.

Admitting a peer is a DECISION, not a request, so it is a command an operator
runs and not an endpoint the adapter exposes. An adapter endpoint would mean
anything able to reach the adapter could widen who this network deals with,
which is exactly the thing admission exists to control.

It lives beside setup.py because it does the same job: both mint a Keycloak
token and write Participant records into this network's registry. setup.py seeds
the network's own participants; this admits another network as a peer.

    oanfed.py publish    generate and sign this network's two published documents
    oanfed.py verify     fetch another network's documents and check them
    oanfed.py admit      record the decision to deal with a peer; also the refresh
    oanfed.py suspend    stop dealing with a peer
    oanfed.py reinstate  undo a suspension; a peer cannot do this for itself

Run a command with -h for its flags.
"""

import argparse, base64, http.client, json, pathlib, ssl, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization as ser

# The two fixed paths. They are NOT configurable: a peer derives them from the
# networkId alone, which is the whole reason a network can be found knowing
# nothing but its domain.
WELL_KNOWN_DESCRIPTOR = "/.well-known/openagrinet"
WELL_KNOWN_JWKS = "/.well-known/jwks.json"

# Caps what will be read from a peer. These documents are small and a peer is
# not yet trusted -- it has not been admitted at the point they are fetched.
# Without a cap, the first thing an unknown host can do to us is stream until we
# run out of memory.
MAX_DOCUMENT_BYTES = 1 << 20

# The only algorithm this implementation signs or verifies with. The contract
# leaves the choice open; narrowing it here means an unexpected value is refused
# rather than quietly treated as this one.
ALGORITHM = "Ed25519"

# The kid on this network's one published key. Stable, because a descriptor's
# signature points at it and a peer resolves the key by it.
KEY_ID = "network-1"

ROLE_NETWORK = "network"


def as_role_list(role):
    """A participant's role, however it was written.

    A list is what the schema now wants; a plain string is what older records
    carry, and nothing rewrites a record just to change its shape.
    """
    if role is None:
        return []
    return list(role) if isinstance(role, list) else [role]


def has_role(record, role):
    return any(str(r).lower() == role.lower() for r in as_role_list(record.get("role")))
STATUS_ACTIVE, STATUS_INACTIVE = "active", "inactive"


# --- canonical form -------------------------------------------------------

def canonical(descriptor):
    """The exact bytes that get signed: RFC 8785 (JCS).

    The signature is excluded, because it cannot cover itself. Everything else
    is included: narrowing the covered set would let an unsigned field be
    changed in flight, and the fields most worth tampering with -- status,
    revision, the discovery URL -- are exactly the ones a smaller set would be
    tempted to omit.

    JCS, and not "whatever this language's JSON encoder happens to emit",
    because the point of a canonical form is that a DIFFERENT implementation
    rebuilds the same bytes. An encoder-specific form works perfectly until
    someone else writes the second implementation, and then fails every
    signature check on documents nobody tampered with.

    Our descriptors contain only strings, integers, arrays and objects, so the
    parts of JCS that are genuinely hard -- number formatting -- do not arise.
    What matters here is: keys sorted, no insignificant whitespace, UTF-8, and
    no escaping beyond what JSON requires.
    """
    without_signature = {k: v for k, v in descriptor.items() if k != "signature"}
    return json.dumps(
        without_signature,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# --- keys -----------------------------------------------------------------

def b64url(raw):
    """base64url WITHOUT padding, which is what RFC 7517 says a JWKS carries."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64url(encoded):
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def registry_key(jwk_x):
    """A JWKS key in the form the registry stores.

    A JWKS carries base64url without padding. The registry's PublicKey schema
    pins ^[A-Za-z0-9+/]{43}=$, which is standard base64 WITH padding. Both are
    correct for their own format, so something has to convert -- and it is one
    function rather than an inline re-encode at each call site, because getting
    it wrong produces a key that is silently the wrong bytes rather than an
    error.
    """
    return base64.b64encode(unb64url(jwk_x)).decode()


def load_or_create_key(path):
    """Read a private key, generating one if the file is absent.

    Stored as the raw 32-byte seed, base64url-unpadded -- the same encoding the
    public half uses in the JWKS, so an operator reading either file sees one
    format rather than two.
    """
    path = pathlib.Path(path)
    if path.exists():
        raw = unb64url(path.read_text().strip())
        # 32 is the seed, which is what this tool writes. 64 is Go's
        # ed25519.PrivateKey -- the seed followed by the public half -- which is
        # what the Go implementation this replaces wrote, and what every key
        # already on disk is. Both are read so an existing network does not have
        # to regenerate its identity to move tools.
        if len(raw) == 64:
            raw = raw[:32]
        if len(raw) != 32:
            raise FederationError(
                f"{path} holds {len(raw)} bytes; an Ed25519 private key is 32 (a seed) "
                f"or 64 (a seed followed by its public half)")
        return Ed25519PrivateKey.from_private_bytes(raw)

    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(
        encoding=ser.Encoding.Raw,
        format=ser.PrivateFormat.Raw,
        encryption_algorithm=ser.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(b64url(raw) + "\n")
    path.chmod(0o600)
    return private


def public_of(private):
    return private.public_key().public_bytes(
        encoding=ser.Encoding.Raw, format=ser.PublicFormat.Raw
    )


# --- sign and verify ------------------------------------------------------

class FederationError(Exception):
    """Anything a caller is expected to act on rather than crash over."""


class StaleRevision(FederationError):
    """The descriptor does not advance the revision.

    Its own type because admit DOUBLES AS THE REFRESH JOB: when it runs on a
    schedule, finding nothing new is the normal outcome, not a failure. Treating
    it as an error would mean a cron that alerts on every quiet run -- and an
    operator who stops reading the alerts.

    A LOWER revision is a replay and a real refusal; the same revision is just
    nothing to do. Both arrive here, and the caller tells them apart by
    comparing the numbers it already holds.
    """


def sign(descriptor, key_id, private):
    """Return a copy of the descriptor carrying its signature.

    The governance key signs this and nothing else. The contract forbids it from
    signing ordinary traffic, which is why admission deliberately keeps it out
    of the registry record a peer's calls are verified against.
    """
    if not descriptor.get("networkId"):
        raise FederationError("descriptor carries no networkId")
    signed = dict(descriptor)
    signed["signature"] = {
        "keyId": key_id,
        "algorithm": ALGORITHM,
        "value": b64url(private.sign(canonical(descriptor))),
    }
    return signed


def verify(descriptor, keyset):
    """Check a descriptor against the keys its network published.

    It answers ONE question -- did the holder of a published key write this
    document -- and deliberately not whether the network should be trusted. The
    keys came from the same domain as the document, so a network can always
    verify its own lies. Admission is what decides trust.
    """
    signature = descriptor.get("signature")
    if not signature:
        raise FederationError("descriptor carries no signature")
    if signature.get("algorithm") != ALGORITHM:
        raise FederationError(f"unsupported signature algorithm: {signature.get('algorithm')!r}")

    key_id = signature.get("keyId")
    jwk = next((k for k in keyset.get("keys", []) if k.get("kid") == key_id), None)
    if jwk is None:
        raise FederationError(f"no published key matches the signature's keyId {key_id!r}")

    raw = unb64url(jwk["x"])
    if len(raw) != 32:
        raise FederationError(f"key {key_id!r} is {len(raw)} bytes, want 32")

    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(
            unb64url(signature["value"]), canonical(descriptor)
        )
    except Exception as err:
        raise FederationError("signature does not verify") from err


# --- fetching a peer's documents ------------------------------------------

class ResolvingHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a given address while still verifying the peer's own name.

    The way `curl --resolve` works. The request still carries the networkId as
    its Host and the certificate is still checked against it, so nothing about
    verification is relaxed -- only where the connection goes. It exists because
    a network id is a DNS domain, and an operator's own machine usually cannot
    resolve one belonging to a stack running in containers.
    """

    def __init__(self, server_name, address, **kwargs):
        host, _, port = address.partition(":")
        super().__init__(host, int(port or 443), **kwargs)
        self._server_name = server_name

    def connect(self):
        super(http.client.HTTPSConnection, self).connect()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._server_name)


class Fetcher:
    """Retrieves and verifies another network's published documents.

    scheme is "https" in any real deployment. It is settable because a local POC
    has no certificate authority and cannot prove domain ownership -- which is
    precisely the property a real deployment relies on, so this is a knob that
    must never be turned in one.
    """

    def __init__(self, scheme="https", ca_file=None, resolve=None, timeout=10):
        self.scheme, self.resolve, self.timeout = scheme or "https", resolve, timeout
        # Real certificate verification against a DIFFERENT root, not skipping
        # it: a peer presenting a certificate this CA did not sign, or one whose
        # name does not match, is still refused. A local stack has no publicly
        # trusted certificate, and the alternative -- turning verification off --
        # would quietly remove the check in the one place a POC is meant to
        # prove it.
        self.context = ssl.create_default_context(cafile=ca_file) if ca_file else None

    def fetch(self, network_id):
        """Retrieve a network's descriptor and keys, and verify one against the other.

        A verified descriptor is a CANDIDATE, not a trusted peer. Both documents
        came from the same host, so this proves self-consistency and nothing
        more -- any network can sign its own claims. Admission is the separate,
        local decision that turns a candidate into a peer.
        """
        if not network_id:
            raise FederationError("no networkId given")

        descriptor = self._get(network_id, WELL_KNOWN_DESCRIPTOR)
        keyset = self._get(network_id, WELL_KNOWN_JWKS)

        # The descriptor says who it is for. A document served at one domain
        # that names another is either a misconfiguration or an attempt to be
        # admitted under someone else's name, and neither should get further.
        if descriptor.get("networkId") != network_id:
            raise FederationError(
                f"{network_id} publishes a descriptor for {descriptor.get('networkId')!r}")
        verify(descriptor, keyset)
        return descriptor, keyset

    def _get(self, network_id, path):
        where = f"{self.scheme}://{network_id}{path}"
        try:
            if self.scheme == "https":
                connection = (
                    ResolvingHTTPSConnection(network_id, self.resolve,
                                             context=self.context, timeout=self.timeout)
                    if self.resolve else
                    http.client.HTTPSConnection(network_id, context=self.context,
                                                timeout=self.timeout)
                )
            else:
                host = self.resolve or network_id
                connection = http.client.HTTPConnection(host, timeout=self.timeout)

            connection.request("GET", path, headers={"Accept": "application/json",
                                                     "Host": network_id})
            response = connection.getresponse()
            if response.status != 200:
                raise FederationError(f"{where} returned {response.status} {response.reason}")
            body = response.read(MAX_DOCUMENT_BYTES)
        except FederationError:
            raise
        except Exception as err:
            raise FederationError(f"fetching {where}: {err}") from err
        finally:
            try:
                connection.close()
            except Exception:
                pass

        try:
            return json.loads(body)
        except json.JSONDecodeError as err:
            raise FederationError(f"parsing {where}: {err}") from err


# --- admission ------------------------------------------------------------

DEFAULT_PROJECTION_TTL = 3600  # seconds; finite, and deliberately short


def projection_ttl(descriptor):
    """What a descriptor permits, in seconds.

    The three cases are genuinely different and none may be folded into
    another: absent is consent to decide for ourselves, zero is a refusal to be
    cached, and a positive value is a bounded licence.
    """
    declared = descriptor.get("projectionTtl")
    if declared is None:
        return DEFAULT_PROJECTION_TTL
    if declared < 0:
        raise FederationError(f"projectionTtl is negative: {declared}")
    if declared == 0:
        raise FederationError(
            "peer declares projectionTtl 0 (live query only), which is not implemented")
    return declared


def published_keys(keyset):
    """Every key the peer published.

    Nothing is stripped. An earlier design dropped the key that signed the
    descriptor, so a peer could not sign ordinary traffic with it -- that split
    is gone, and a network now publishes ONE key which does both jobs.
    """
    return list(keyset.get("keys", []))


# What an entry is in relation to us.
#
# self      this deployment's own entry
# source    a peer we consume from -- our crawler asks it for its catalogs
# requester a peer that consumes from us -- it calls us and we verify its
#           signatures, but we do not crawl it
# both      a peer in both directions, which is what mutual admission produces
FEDERATION_ROLES = ("self", "source", "requester", "both")
DEFAULT_FEDERATION_ROLE = "both"


def admit_record(current, fresh, keyset, federation_role=DEFAULT_FEDERATION_ROLE):
    """Turn a verified descriptor into the registry record to write.

    current is None on first admission; on every later call it is what the
    registry already holds, which is what makes the revision check possible.

    Two rules, and each exists because of a specific way a peer could otherwise
    take something back or take something over:

      - The revision must increase, so a superseded descriptor cannot be
        replayed. That, plus the documents being served from the network's own
        domain, is what the contract rests identity on.
      - Status is carried over and never read from the descriptor, so a peer an
        operator suspended cannot reinstate itself by publishing a new file.
        What the peer says about itself is a claim; what we recorded is a
        decision.
    """
    network_id = fresh.get("networkId")
    if not network_id:
        raise FederationError("descriptor carries no networkId")

    ttl = projection_ttl(fresh)
    status = STATUS_ACTIVE
    if current is not None:
        if (current.get("participantId") or "").lower() != network_id.lower():
            raise FederationError(
                f"descriptor is for {network_id!r}, stored peer is "
                f"{current.get('participantId')!r}")
        held = current.get("revision") or 0
        if fresh.get("revision", 0) <= held:
            raise StaleRevision(
                f"descriptor revision did not increase: hold {held}, "
                f"offered {fresh.get('revision', 0)}")
        status = current.get("status", STATUS_ACTIVE)

    admitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "participantId": network_id,
        "name": fresh.get("name", ""),
        # A peer is a node, not an upstream: it signs, and its signatures are
        # verified against the keys recorded here.
        "type": "node",
        "status": status,
        "baseUrl": (fresh.get("services") or {}).get("discovery", ""),
        # A LIST, because role says what a participant serves and a network can
        # serve several layers. We only know this peer as a network -- what it
        # runs behind that is its own business and not in its descriptor.
        "role": [ROLE_NETWORK],
        "keys": [
            {
                "alg": "ed25519",
                "key": registry_key(k["x"]),
                # validFrom is required, and is recorded as the moment we
                # admitted the peer -- the only moment we can actually attest
                # to. The peer's published documents carry no validity window,
                # and inventing one would be recording a claim nobody made.
                "validFrom": admitted_at,
                "status": "active",
            }
            for k in published_keys(keyset)
        ],
        "revision": fresh.get("revision", 0),
        "projectionTtl": ttl,
        # Stored VERBATIM, not interpreted. What a peer can speak is its
        # statement; rewriting it here would make our copy disagree with the
        # document we read it from. The crawler derives the context URL it
        # filters on from these at crawl time.
        "schemaPacks": list(fresh.get("schemaPacks") or []),
        # Carried over on a refresh. A later descriptor says nothing about the
        # relationship -- that is OUR decision about a peer, not a claim it
        # makes about itself -- so refreshing must not quietly widen it.
        "federationRole": (current or {}).get("federationRole") or federation_role,
    }


# --- this network's own registry ------------------------------------------

class ReadOnlyRegistry:
    """What publish needs: searches, and no credentials.

    Separate from Registry because the privilege is different. Writing a
    participant is an operator's decision and needs a Keycloak token; reading
    one back to describe ourselves needs nothing, because the search endpoint is
    public. Sharing one class would mean publish asking for a password it has no
    use for.
    """

    def __init__(self, url, timeout=30):
        self.url, self.timeout = url.rstrip("/"), timeout

    def _search(self, entity, filters):
        request = urllib.request.Request(
            f"{self.url}/api/v1/{entity}/search",
            data=json.dumps({"filters": filters}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                found = json.load(response)
        except urllib.error.HTTPError as err:
            raise FederationError(
                f"registry search {entity} returned {err.code}: "
                f"{err.read().decode()[:200]}") from err
        except Exception as err:
            raise FederationError(f"reaching the registry at {self.url}: {err}") from err
        return found.get("data", found) if isinstance(found, dict) else found

    def participant(self, participant_id):
        rows = self._search("Participant", {"participantId": {"eq": participant_id}})
        return rows[0] if rows else None

    def schema_packs(self):
        """The schema pack URLs this deployment records itself as speaking.

        Active only: a retired capability should stop being advertised without
        anyone having to remember to edit a descriptor.
        """
        rows = self._search("SchemaRegistry", {"status": {"eq": "active"}})
        return sorted({r["schemaUrl"] for r in rows if r.get("schemaUrl")})


class Registry:
    """The SunbirdRC registry this network runs.

    The same API surface setup.py uses: a Keycloak token, then POST to create
    and PUT to update. Kept as its own class only so the token is minted once
    per command rather than per call.
    """

    def __init__(self, args):
        self.url = args.registry_url.rstrip("/")
        self.keycloak = args.keycloak_url.rstrip("/")
        self.issuer, self.realm = args.keycloak_issuer, args.realm
        self.client_id, self.username, self.password = args.client_id, args.username, args.password
        self.timeout = args.registry_timeout
        self._bearer = None

    def bearer(self):
        if self._bearer:
            return self._bearer
        body = urllib.parse.urlencode({
            "grant_type": "password", "client_id": self.client_id,
            "username": self.username, "password": self.password,
        }).encode()
        # Keycloak sits behind PROXY_ADDRESS_FORWARDING, so it builds the
        # token's issuer from the Host header. The registry validates that
        # issuer against its own configured address -- the CONTAINER-INTERNAL
        # one -- so a token minted without this header is rejected with a 401
        # that says nothing useful.
        endpoint = (f"{self.keycloak}/auth/realms/{self.realm}"
                    f"/protocol/openid-connect/token")
        request = urllib.request.Request(
            endpoint, data=body,
            headers={"X-Forwarded-Host": self.issuer, "X-Forwarded-Proto": "http",
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as err:
            raise FederationError(
                f"keycloak {endpoint} returned {err.code}: {err.read().decode()[:200]}") from err
        except Exception as err:
            raise FederationError(f"reaching keycloak at {endpoint}: {err}") from err
        if not payload.get("access_token"):
            # Keycloak answers 200 with an empty body when the forwarded host
            # does not match what the registry expects, so a missing token is
            # its own case rather than an HTTP error.
            raise FederationError(
                "keycloak issued no token -- check the credentials and --keycloak-issuer")
        self._bearer = payload["access_token"]
        return self._bearer

    def _call(self, method, path, body=None, authenticated=True):
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self.bearer()}"
        request = urllib.request.Request(
            f"{self.url}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            raise FederationError(
                f"registry {method} {path} returned {err.code}: {err.read().decode()[:300]}"
            ) from err
        return json.loads(raw) if raw else None

    def find(self, participant_id):
        """The stored record for a participant, or None.

        Only participantId is filtered on: it is the schema's uniqueIndexFields,
        so the registry already guarantees at most one match.
        """
        found = self._call("POST", "/api/v1/Participant/search",
                           {"filters": {"participantId": {"eq": participant_id}}},
                           authenticated=False)
        records = found.get("data", found) if isinstance(found, dict) else found
        return records[0] if records else None

    def write(self, record, existing):
        """Create a record, or update the one already there."""
        if existing and existing.get("osid"):
            return self._call("PUT", f"/api/v1/Participant/{existing['osid']}", record)
        return self._call("POST", "/api/v1/Participant", record)

    def set_status(self, participant_id, status):
        """Change only the status of an existing record.

        Nothing is fetched from the peer. Suspension is a decision about a
        network we have stopped trusting, and asking that network's own server
        to take part in it would be absurd.
        """
        existing = self.find(participant_id)
        if not existing:
            raise FederationError(f"{participant_id} is not in this registry")
        if not has_role(existing, ROLE_NETWORK):
            raise FederationError(f"{participant_id} is not a peer network")
        updated = {k: v for k, v in existing.items() if not k.startswith("os")}
        updated["status"] = status
        # Normalised on the way out. A record written before role became a list
        # carries a plain string, and sending it back unchanged is refused --
        # the schema now wants an array. Reading either shape and writing one is
        # what lets both sit in the registry at once.
        updated["role"] = as_role_list(existing.get("role"))
        self.write(updated, existing)
        return updated


# --- commands -------------------------------------------------------------

def cmd_publish(args):
    """Generate and sign this network's two published documents FROM THE REGISTRY.

    The registry entry is the source of truth, not these flags. What a network
    says about itself in public is then the same thing it holds about itself
    internally, by construction -- rather than two descriptions of one network
    that happen to agree on the day someone typed them.

    Reading it needs no credentials: the registry's search endpoint is public.
    Writing an entry is the privileged act, and that is setup.py's job, not this
    one's.
    """
    out = pathlib.Path(args.out) / ".well-known"
    out.mkdir(parents=True, exist_ok=True)

    registry = ReadOnlyRegistry(args.registry_url, args.registry_timeout)
    entry = registry.participant(args.network_id)
    if entry is None:
        raise FederationError(
            f"{args.network_id} is not in this registry -- seed it first, "
            f"then publish from it")

    network_key = load_or_create_key(args.key)
    published = registry_key(b64url(public_of(network_key)))
    held = [k.get("key", "").removeprefix("base64:") for k in entry.get("keys", [])]
    if published not in held:
        # Publishing a key the registry does not hold would hand peers something
        # that verifies our descriptor but nothing else -- our signed traffic
        # would be checked against the registry's key and fail. Worth refusing
        # loudly rather than discovering on the first cross-network call.
        raise FederationError(
            f"{args.key} is not the key {args.network_id} is registered with; "
            f"publishing it would sign descriptors with one key and traffic with another")

    descriptor = {
        "version": "0.1",
        "networkId": entry["participantId"],
        "name": entry.get("name") or entry["participantId"],
        "operator": {"name": args.operator or entry.get("name") or entry["participantId"]},
        "revision": args.revision,
        "status": args.status,
        "issuedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "operatingProfile": [p for p in args.operating_profile.split(",") if p],
        # From the schema registry, which is where this deployment records what
        # it can speak. Typing them again here would let the two disagree.
        "schemaPacks": registry.schema_packs(),
        # The entry's baseUrl IS the federated discovery endpoint -- the same
        # field admission reads back out of a peer's descriptor, so what we
        # publish and what a peer stores about us are one string.
        "services": {"discovery": entry.get("baseUrl", "")},
    }
    if args.projection_ttl is not None:
        descriptor["projectionTtl"] = args.projection_ttl

    # ONE key. It signs this descriptor and it signs cross-network traffic.
    #
    # An earlier design split these -- a governance key for the descriptor, an
    # operational key for everything else -- so that holding the traffic key
    # could not let you rewrite who the network is. That is a real property, and
    # it is given up deliberately: two keys meant two identities to configure,
    # two to rotate, and a rule ("never sign traffic with the governance key")
    # that nothing downstream could enforce, because a signature validator sees
    # only that a key is published and active.
    signed = sign(descriptor, KEY_ID, network_key)
    keyset = {"keys": [
        {"kty": "OKP", "crv": "Ed25519", "kid": KEY_ID, "x": b64url(public_of(network_key))},
    ]}

    (out / "openagrinet").write_text(json.dumps(signed, indent=2) + "\n")
    (out / "jwks.json").write_text(json.dumps(keyset, indent=2) + "\n")

    print(f"{args.network_id} now hosts its descriptor at revision {args.revision}")
    print(f"  descriptor {out / 'openagrinet'}")
    print(f"  keys       {out / 'jwks.json'}")
    return 0


def cmd_verify(args):
    """Fetch another network's documents and check them."""
    descriptor, keyset = fetcher_from(args).fetch(args.network_id)
    print(f"verified {descriptor['networkId']} at revision {descriptor.get('revision')}, "
          f"status {descriptor.get('status')}")
    print(f"  discovery  {(descriptor.get('services') or {}).get('discovery')}")
    print(f"  keys       {len(keyset.get('keys', []))} published")
    print("  this proves the documents are self-consistent, NOT that the network "
          "should be trusted -- admission is that decision")
    return 0


def cmd_admit(args):
    """Record the decision to deal with a peer. Also the refresh job."""
    descriptor, keyset = fetcher_from(args).fetch(args.network_id)

    registry = Registry(args)
    existing = registry.find(args.network_id)
    try:
        record = admit_record(existing, descriptor, keyset, args.federation_role)
    except StaleRevision:
        # Nothing written, and not a failure: this command is also the refresh
        # job, and a refresh that finds nothing new is its normal outcome.
        print(f"{args.network_id} is unchanged at revision "
              f"{existing.get('revision')}; nothing written")
        return 0
    registry.write(record, existing)

    verb = "refreshed" if existing else "admitted"
    print(f"{verb} {record['participantId']} at revision {record['revision']}, "
          f"status {record['status']}")
    print(f"  discovery     {record['baseUrl']}")
    print(f"  projectionTtl {record['projectionTtl']}s")
    print(f"  keys          {len(record['keys'])} published")
    print(f"  relationship  {record['federationRole']}")
    return 0


def cmd_suspend(args):
    """Stop dealing with a peer."""
    Registry(args).set_status(args.network_id, STATUS_INACTIVE)
    print(f"suspended {args.network_id}; it is no longer crawled and no longer answered")
    return 0


def cmd_reinstate(args):
    """Undo a suspension. A peer cannot do this for itself."""
    Registry(args).set_status(args.network_id, STATUS_ACTIVE)
    print(f"reinstated {args.network_id}")
    return 0


def fetcher_from(args):
    return Fetcher(scheme=args.scheme, ca_file=args.ca_file or None,
                   resolve=args.resolve or None, timeout=args.timeout)


# --- flags ----------------------------------------------------------------

def add_peer_access(parser):
    """How an operator reaches a peer's published documents.

    Shared by verify and admit so the two cannot disagree about what
    verification means -- the difference between them is what they do with the
    result, not how they obtain it.
    """
    parser.add_argument("--scheme", default="https",
                        help="http only for a stack with no certificates at all")
    parser.add_argument("--ca-file", default="",
                        help="verify the peer's certificate against the CAs in this PEM file")
    parser.add_argument("--resolve", default="",
                        help="reach the peer at this host:port instead of resolving its domain")
    parser.add_argument("--timeout", type=float, default=10, help="per-request timeout")


def add_registry(parser):
    parser.add_argument("--registry-url", required=True,
                        help="this network's own registry, e.g. http://localhost:10081")
    parser.add_argument("--keycloak-url", required=True,
                        help="the registry's Keycloak, e.g. http://localhost:10080")
    parser.add_argument("--keycloak-issuer", default="sunbird-registry-keycloak:8080",
                        help="the Host the registry expects the token's issuer to carry")
    parser.add_argument("--realm", default="sunbird-rc", help="Keycloak realm")
    parser.add_argument("--client-id", default="registry-frontend", help="Keycloak client id")
    parser.add_argument("--username", required=True, help="registry admin user")
    parser.add_argument("--password", required=True, help="registry admin password")
    parser.add_argument("--registry-timeout", type=float, default=30,
                        help="per-request timeout for our own registry")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="oanfed.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="generate and sign this network's documents")
    publish.add_argument("--network-id", required=True,
                         help="this network's DNS domain, which is its identity")
    publish.add_argument("--registry-url", required=True,
                         help="this network's own registry, which holds the entry "
                              "this descriptor is generated FROM")
    publish.add_argument("--registry-timeout", type=float, default=30,
                         help="per-request timeout for the registry")
    publish.add_argument("--operator", default="",
                         help="organisation answering for this network; "
                              "defaults to the registered name")
    publish.add_argument("--revision", type=int, required=True,
                         help="descriptor revision; must increase on every republish")
    publish.add_argument("--operating-profile", default="OAN/v0.1,Beckn/v2",
                         help="comma-separated version stack")
    publish.add_argument("--status", default="Active", help="Active, Suspended or Retired")
    publish.add_argument("--projection-ttl", type=int, default=None,
                         help="seconds a peer may keep what it crawls; omit to let peers decide")
    publish.add_argument("--out", required=True, help="directory to write .well-known/ into")
    publish.add_argument("--key", required=True,
                         help="this network's private key file; it signs the descriptor "
                              "AND cross-network traffic. Generated if absent")
    publish.set_defaults(run=cmd_publish)

    verify_cmd = commands.add_parser("verify", help="fetch and check another network's documents")
    verify_cmd.add_argument("--network-id", required=True, help="the peer to check")
    add_peer_access(verify_cmd)
    verify_cmd.set_defaults(run=cmd_verify)

    admit = commands.add_parser("admit", help="record the decision to deal with a peer")
    admit.add_argument("--network-id", required=True,
                       help="the peer to admit, which is its domain")
    admit.add_argument("--federation-role", default=DEFAULT_FEDERATION_ROLE,
                       choices=[r for r in FEDERATION_ROLES if r != "self"],
                       help="what this peer is to us: source (we crawl it), "
                            "requester (it calls us), or both. Only set on first "
                            "admission -- a refresh keeps what was decided")
    add_peer_access(admit)
    add_registry(admit)
    admit.set_defaults(run=cmd_admit)

    suspend = commands.add_parser("suspend", help="stop dealing with a peer")
    suspend.add_argument("--network-id", required=True, help="the peer to suspend")
    add_registry(suspend)
    suspend.set_defaults(run=cmd_suspend)

    reinstate = commands.add_parser("reinstate", help="undo a suspension")
    reinstate.add_argument("--network-id", required=True, help="the peer to reinstate")
    add_registry(reinstate)
    reinstate.set_defaults(run=cmd_reinstate)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except FederationError as err:
        print(f"oanfed {args.command}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
