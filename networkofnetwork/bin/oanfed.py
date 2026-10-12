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

import argparse, base64, hashlib, http.client, json, os, pathlib, re, ssl, sys, urllib.error, urllib.parse, urllib.request
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

# The only signature algorithm this implementation signs or verifies with, by
# its fully-specified JOSE name (RFC 9864, which deprecates the polymorphic
# "EdDSA"). The contract leaves the choice open; narrowing it here means an
# unexpected value is refused rather than quietly treated as this one.
ALGORITHM = "Ed25519"

# The Network Descriptor schema version this implementation writes and reads. A
# version it does not know is refused rather than half-understood.
DESCRIPTOR_VERSION = "1.0"
DESCRIPTOR_STATUSES = ("Active", "Suspended", "Retired")

# How far ahead of our clock a peer's issuedAt may be before it is refused. A
# document from the future is a clock fault or a pre-signed replay waiting to
# happen; a few minutes absorbs ordinary skew.
MAX_CLOCK_SKEW_SECONDS = 300

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


class FederationError(Exception):
    """Anything a caller is expected to act on rather than crash over."""


# --- canonical form -------------------------------------------------------

# JCS serialises numbers the way ECMAScript does, which is exact for integers
# only up to 2**53. Beyond it two implementations can disagree on the digits.
MAX_SAFE_INTEGER = 2 ** 53 - 1


def canonical(descriptor):
    """The exact bytes that get signed: RFC 8785 (JCS).

    The signature is excluded, because it cannot cover itself. Everything else
    is included: narrowing the covered set would let an unsigned field be
    changed in flight, and the fields most worth tampering with -- status,
    revision, the discovery URL, which keys may sign traffic -- are exactly the
    ones a smaller set would be tempted to omit.

    JCS, and not "whatever this language's JSON encoder happens to emit",
    because the point of a canonical form is that a DIFFERENT implementation
    rebuilds the same bytes.
    """
    without_signature = {k: v for k, v in descriptor.items() if k != "signature"}
    return _jcs(without_signature).encode("utf-8")


def _jcs(value):
    """One JCS value. Floats are refused rather than formatted.

    A descriptor has no use for a fraction, and number formatting is the one
    part of JCS where implementations genuinely diverge -- so the safe rule is
    that a non-integer never reaches a signature.
    """
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise FederationError(f"integer {value} is outside the range JCS signs exactly")
        return str(value)
    if isinstance(value, float):
        raise FederationError(f"non-integer number {value!r} cannot be signed; use an integer")
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as err:
            raise FederationError("string is not valid Unicode (lone surrogate)") from err
        # Python's escaping is JCS's: \b \f \n \r \t \" \\ as short forms, other
        # controls as lowercase \u00xx, everything else (including '/') raw.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_jcs(v) for v in value) + "]"
    if isinstance(value, dict):
        # JCS sorts by UTF-16 code units, not code points. The two differ only
        # above U+FFFF, which is exactly where a naive sort_keys goes wrong.
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(f"{_jcs(k)}:{_jcs(value[k])}" for k in keys) + "}"
    raise FederationError(f"{type(value).__name__} cannot be signed")


# --- keys -----------------------------------------------------------------

def b64url(raw):
    """base64url WITHOUT padding, which is what RFC 7517 says a JWKS carries."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64url(encoded):
    if not isinstance(encoded, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", encoded):
        raise FederationError("value is not base64url")
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


def jwk(public):
    """The JWK for a raw Ed25519 public key.

    The kid is the key's RFC 7638 thumbprint: derived from the key itself, so it
    never needs configuring, can never point at the wrong key, and changes on
    its own when a key is rotated -- which is what lets old and new overlap in
    one JWKS without colliding.
    """
    x = b64url(public)
    members = {"crv": "Ed25519", "kty": "OKP", "x": x}
    thumbprint = hashlib.sha256(_jcs(members).encode()).digest()
    return {"kty": "OKP", "crv": "Ed25519", "alg": ALGORITHM, "use": "sig",
            "kid": b64url(thumbprint), "x": x}


def load_or_create_key(path):
    """Read the governance private key, generating one if the file is absent.

    Stored as the raw 32-byte seed, base64url-unpadded -- the same encoding the
    public half uses in the JWKS, so an operator reading either file sees one
    format rather than two.

    A key file anyone but its owner can read is refused, not used. This key
    decides what the network says about itself, including which keys may sign
    as it; a copy of it is the network.
    """
    path = pathlib.Path(path)
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise FederationError(
                f"{path} is readable by group or others; chmod 600 it before signing with it")
        raw = unb64url(path.read_text().strip())
        # 64 is Go's ed25519.PrivateKey -- the seed followed by the public half.
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
    # Created 0600 rather than chmod-ed after: no window where it is readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(b64url(raw) + "\n")
    return private


def public_of(private):
    return private.public_key().public_bytes(
        encoding=ser.Encoding.Raw, format=ser.PublicFormat.Raw
    )


# --- the published documents: shape ---------------------------------------

def _is_https_url(value):
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname)


def _parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise FederationError(f"issuedAt {value!r} is not a UTC timestamp like 2026-09-17T10:00:00Z")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as err:
        raise FederationError(f"issuedAt {value!r} is not a real date") from err


def _nonempty_str(value):
    return isinstance(value, str) and value.strip() != ""


def validate_keyset(keyset):
    """A JWKS, checked; returns {kid: raw public key} for its Ed25519 keys.

    Keys of other types are allowed and skipped -- a network may publish more
    than this implementation verifies with. A duplicate kid is refused outright:
    with two keys under one name, "the key the signature names" means nothing.
    """
    if not isinstance(keyset, dict) or not isinstance(keyset.get("keys"), list):
        raise FederationError("JWKS is not an object with a keys array")
    found, seen = {}, set()
    for entry in keyset["keys"]:
        if not isinstance(entry, dict) or not _nonempty_str(entry.get("kid")):
            raise FederationError("JWKS holds a key without a kid")
        kid = entry["kid"]
        if kid in seen:
            raise FederationError(f"JWKS holds two keys with kid {kid!r}")
        seen.add(kid)
        if entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
            continue
        if entry.get("alg", ALGORITHM) != ALGORITHM:
            raise FederationError(f"key {kid!r} declares alg {entry.get('alg')!r}")
        if entry.get("use", "sig") != "sig":
            raise FederationError(f"key {kid!r} is not a signing key")
        raw = unb64url(entry.get("x", ""))
        if len(raw) != 32:
            raise FederationError(f"key {kid!r} is {len(raw)} bytes, want 32")
        found[kid] = raw
    return found


def validate_descriptor(descriptor, now=None):
    """The Network Descriptor contract, field by field.

    Run before signing, so this network never publishes something a peer must
    refuse, and after fetching, so a peer's document is checked against the
    same rules rather than a looser reading of them.
    """
    if not isinstance(descriptor, dict):
        raise FederationError("descriptor is not a JSON object")

    def need(ok, message):
        if not ok:
            raise FederationError(message)

    need(descriptor.get("version") == DESCRIPTOR_VERSION,
         f"descriptor version {descriptor.get('version')!r} is not {DESCRIPTOR_VERSION!r}")
    network_id = descriptor.get("networkId")
    need(isinstance(network_id, str)
         and re.fullmatch(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}",
                          network_id) is not None,
         f"networkId {network_id!r} is not a lowercase DNS domain")
    need(_nonempty_str(descriptor.get("name")), "name is missing")
    operator = descriptor.get("operator")
    need(isinstance(operator, dict) and _nonempty_str(operator.get("name")),
         "operator.name is missing")
    need("operatorId" not in operator or _nonempty_str(operator["operatorId"]),
         "operator.operatorId is empty")
    revision = descriptor.get("revision")
    need(isinstance(revision, int) and not isinstance(revision, bool)
         and 1 <= revision <= MAX_SAFE_INTEGER,
         f"revision {revision!r} is not a positive integer")
    need(descriptor.get("status") in DESCRIPTOR_STATUSES,
         f"status {descriptor.get('status')!r} is not one of {', '.join(DESCRIPTOR_STATUSES)}")
    issued = _parse_time(descriptor.get("issuedAt"))
    now = now or datetime.now(timezone.utc)
    need((issued - now).total_seconds() <= MAX_CLOCK_SKEW_SECONDS,
         f"issuedAt {descriptor['issuedAt']} is in the future")
    profile = descriptor.get("operatingProfile")
    need(isinstance(profile, list) and profile and all(_nonempty_str(p) for p in profile),
         "operatingProfile is not a non-empty list of versions")
    packs = descriptor.get("schemaPacks")
    need(isinstance(packs, list) and all(_is_https_url(p) for p in packs)
         and len(set(packs)) == len(packs),
         "schemaPacks is not a list of distinct https URLs")
    services = descriptor.get("services")
    need(isinstance(services, dict) and _is_https_url(services.get("discovery")),
         "services.discovery is not an https URL")
    contacts = descriptor.get("contacts")
    need(contacts is None or (isinstance(contacts, dict)
                              and all(_nonempty_str(v) for v in contacts.values())),
         "contacts is not an object of strings")
    ttl = descriptor.get("projectionTtl")
    need(ttl is None or (isinstance(ttl, int) and not isinstance(ttl, bool) and ttl >= 0),
         f"projectionTtl {ttl!r} is not a non-negative integer")
    delegated = descriptor.get("operationalKeys")
    need(isinstance(delegated, list) and delegated
         and all(_nonempty_str(k) for k in delegated) and len(set(delegated)) == len(delegated),
         "operationalKeys is not a non-empty list of distinct kids")


# --- sign and verify ------------------------------------------------------

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


def sign(descriptor, private):
    """Return a copy of the descriptor carrying the governance signature.

    The governance key signs this and nothing else. It is named by its own
    thumbprint, so the signature cannot point at a different key than the one
    that made it.
    """
    signed = {k: v for k, v in descriptor.items() if k != "signature"}
    signed["signature"] = {
        "keyId": jwk(public_of(private))["kid"],
        "algorithm": ALGORITHM,
        "value": b64url(private.sign(canonical(signed))),
    }
    return signed


def verify(descriptor, keyset, now=None):
    """Check a descriptor, its shape, and its signature against the JWKS.

    It answers ONE question -- did the holder of a published governance key
    write this document -- and deliberately not whether the network should be
    trusted. The keys came from the same domain as the document, so a network
    can always verify its own lies. Admission is what decides trust.

    The key that signed the descriptor must not also be one it delegates for
    traffic: the contract says the governance key never signs normal traffic,
    and this is the one place that can be checked.
    """
    validate_descriptor(descriptor, now)
    keys = validate_keyset(keyset)

    signature = descriptor.get("signature")
    if not isinstance(signature, dict):
        raise FederationError("descriptor carries no signature")
    if signature.get("algorithm") != ALGORITHM:
        raise FederationError(f"unsupported signature algorithm: {signature.get('algorithm')!r}")
    key_id = signature.get("keyId")
    if key_id not in keys:
        raise FederationError(f"no published Ed25519 key matches the signature's keyId {key_id!r}")
    delegated = descriptor["operationalKeys"]
    if key_id in delegated:
        raise FederationError(
            f"governance key {key_id!r} is also delegated for traffic; the two must differ")
    missing = [k for k in delegated if k not in keys]
    if missing:
        raise FederationError(f"operationalKeys not in the JWKS: {', '.join(missing)}")

    try:
        Ed25519PublicKey.from_public_bytes(keys[key_id]).verify(
            unb64url(signature.get("value", "")), canonical(descriptor)
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


def operational_keys(descriptor, keyset):
    """The keys a peer delegated for traffic, and only those.

    Its governance key is published in the same JWKS but deliberately left out:
    whatever is recorded here is what the peer's requests are verified against,
    and the governance key must never pass that check. Which keys are delegated
    comes from the SIGNED descriptor, so a JWKS edit alone cannot add one.
    """
    delegated = set(descriptor.get("operationalKeys") or [])
    return [k for k in keyset.get("keys", []) if k.get("kid") in delegated]


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
            for k in operational_keys(fresh, keyset)
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

def registered_signing_keys(entry, now=None):
    """The entry's Ed25519 keys that are usable now or about to be.

    These are the operational keys: the adapters sign traffic with them and the
    registry already holds them, plural, for rotation overlap. A key not yet
    valid IS included -- a peer has to know a key before it starts signing with
    it. A revoked or expired one is not: publishing it would delegate a key this
    network has already withdrawn.
    """
    now = now or datetime.now(timezone.utc)
    usable = []
    for k in entry.get("keys") or []:
        if str(k.get("alg", "")).lower() != "ed25519" or k.get("status", "active") != "active":
            continue
        until = k.get("validUntil")
        if until and datetime.fromisoformat(until.replace("Z", "+00:00")) <= now:
            continue
        raw = base64.b64decode(str(k.get("key", "")).removeprefix("base64:"))
        if len(raw) != 32:
            raise FederationError(f"registry key {k.get('osid', '?')} is not a 32-byte Ed25519 key")
        usable.append(raw)
    return usable


def last_published_revision(path):
    """The revision already on disk, or 0. Unreadable is an error, not 0.

    Treating a corrupt file as "nothing published" would let a revision go
    backwards, and every peer holding the higher one would refuse ours.
    """
    if not path.exists():
        return 0
    try:
        return int(json.loads(path.read_text())["revision"])
    except Exception as err:
        raise FederationError(f"cannot read the revision in {path}: {err}") from err


def write_atomically(path, document):
    """Replace a published file in one step, so a reader never sees half of it."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def cmd_publish(args):
    """Generate and sign this network's two published documents FROM THE REGISTRY.

    The registry entry is the source of truth, not these flags. What a network
    says about itself in public is then the same thing it holds about itself
    internally, by construction -- rather than two descriptions of one network
    that happen to agree on the day someone typed them.

    Two kinds of key, as the contract requires:

      governance   signs the descriptor and nothing else. Its private half is
                   --governance-key; it is NOT in the registry, so no peer can
                   verify a request signed with it.
      operational  sign traffic. Read from the registry entry; the descriptor
                   delegates them by kid in operationalKeys, under the
                   governance signature.

    Reading the registry needs no credentials: its search endpoint is public.
    """
    out = pathlib.Path(args.out) / ".well-known"
    descriptor_path, jwks_path = out / "openagrinet", out / "jwks.json"

    held = last_published_revision(descriptor_path)
    if args.revision <= held:
        raise FederationError(
            f"revision {args.revision} does not increase on the published {held}; "
            f"every peer holding {held} would refuse it")

    registry = ReadOnlyRegistry(args.registry_url, args.registry_timeout)
    entry = registry.participant(args.network_id)
    if entry is None:
        raise FederationError(
            f"{args.network_id} is not in this registry -- seed it first, "
            f"then publish from it")

    operational = registered_signing_keys(entry)
    if not operational:
        raise FederationError(
            f"{args.network_id} has no active Ed25519 key in the registry to delegate")

    governance = load_or_create_key(args.governance_key)
    if public_of(governance) in operational:
        # The one configuration that silently breaks the contract: the key that
        # vouches for the network would also be accepted on its traffic.
        raise FederationError(
            f"{args.governance_key} is also a registered traffic key; the governance "
            f"key must be separate -- use a new file")

    governance_jwk = jwk(public_of(governance))
    operational_jwks = [jwk(raw) for raw in operational]

    descriptor = {
        "version": DESCRIPTOR_VERSION,
        "networkId": entry["participantId"],
        "name": entry.get("name") or entry["participantId"],
        "operator": {"name": args.operator or entry.get("name") or entry["participantId"]},
        "revision": args.revision,
        "status": args.status,
        "issuedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "operatingProfile": [p.strip() for p in args.operating_profile.split(",") if p.strip()],
        # From the schema registry, which is where this deployment records what
        # it can speak. Typing them again here would let the two disagree.
        "schemaPacks": registry.schema_packs(),
        # The entry's baseUrl IS the federated discovery endpoint -- the same
        # field admission reads back out of a peer's descriptor, so what we
        # publish and what a peer stores about us are one string.
        "services": {"discovery": entry.get("baseUrl", "")},
        # The delegation: which JWKS keys may sign traffic as this network.
        "operationalKeys": [k["kid"] for k in operational_jwks],
    }
    if args.projection_ttl is not None:
        descriptor["projectionTtl"] = args.projection_ttl

    signed = sign(descriptor, governance)
    keyset = {"keys": [governance_jwk, *operational_jwks]}
    # Checked exactly as a peer will check it, before anything is written: a
    # document this network would itself refuse never goes out.
    verify(signed, keyset)

    # Keys first. A peer fetching between the two writes then finds the old
    # descriptor with a JWKS that still verifies it -- never a descriptor naming
    # a key that is not published yet.
    out.mkdir(parents=True, exist_ok=True)
    write_atomically(jwks_path, keyset)
    write_atomically(descriptor_path, signed)

    print(f"{args.network_id} now hosts its descriptor at revision {args.revision}")
    print(f"  descriptor  {descriptor_path}")
    print(f"  keys        {jwks_path}")
    print(f"  governance  {governance_jwk['kid']}")
    print(f"  operational {', '.join(descriptor['operationalKeys'])}")
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
    publish.add_argument("--status", default="Active", choices=DESCRIPTOR_STATUSES,
                         help="what this network declares itself to be")
    publish.add_argument("--projection-ttl", type=int, default=None,
                         help="seconds a peer may keep what it crawls; omit to let peers decide")
    publish.add_argument("--out", required=True, help="directory to write .well-known/ into")
    publish.add_argument("--governance-key", required=True,
                         help="the governance private key file; it signs the descriptor and "
                              "NEVER traffic, so it must not be a registered key. "
                              "Generated (mode 600) if absent")
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
