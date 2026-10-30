#!/usr/bin/env python3
"""oanfed -- publish and verify a network's federation documents.

Served at https://{networkId}/.well-known/: openagrinet (signed descriptor),
jwks.json and schema-packs.json, all generated from our registry entry.

    oanfed.py publish    generate and sign this network's documents
    oanfed.py verify     fetch a network's documents and check them
    oanfed.py admit      record a peer in our registry, in one direction
    oanfed.py refresh    re-read an admitted peer's documents
    oanfed.py suspend    withdraw one direction of a peer's admission
"""

import argparse, base64, binascii, http.client, json, os, pathlib, re, ssl, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization as ser

# Fixed: a peer derives them from the networkId alone.
WELL_KNOWN_DESCRIPTOR = "/.well-known/openagrinet"
WELL_KNOWN_JWKS = "/.well-known/jwks.json"
WELL_KNOWN_SCHEMA_PACKS = "/.well-known/schema-packs.json"

# A peer is not trusted yet when fetched; cap what it can make us read.
MAX_DOCUMENT_BYTES = 1 << 20

# JOSE name per RFC 9864; anything else is refused.
ALGORITHM = "Ed25519"

DESCRIPTOR_VERSION = "1.0"
DESCRIPTOR_STATUSES = ("Active", "Suspended", "Retired")

# How far a peer's issuedAt may run ahead of our clock.
MAX_CLOCK_SKEW_SECONDS = 300

class FederationError(Exception):
    """Anything a caller is expected to act on rather than crash over."""


# --- canonical form -------------------------------------------------------

# JCS numbers are exact only up to 2**53.
MAX_SAFE_INTEGER = 2 ** 53 - 1


def canonical(descriptor):
    """The bytes that get signed: RFC 8785 (JCS) of everything but the signature."""
    without_signature = {k: v for k, v in descriptor.items() if k != "signature"}
    return _jcs(without_signature).encode("utf-8")


def _jcs(value):
    """One JCS value. Floats are refused: their formatting differs across implementations."""
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
        # Python's escaping matches JCS.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_jcs(v) for v in value) + "]"
    if isinstance(value, dict):
        # JCS sorts by UTF-16 code units, not code points.
        keys = sorted(value, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(f"{_jcs(k)}:{_jcs(value[k])}" for k in keys) + "}"
    raise FederationError(f"{type(value).__name__} cannot be signed")


# --- keys -----------------------------------------------------------------

def b64url(raw):
    """base64url without padding, as a JWKS carries it."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64url(encoded):
    if not isinstance(encoded, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", encoded):
        raise FederationError("value is not base64url")
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def jwk(public, kid):
    """The JWK for a raw Ed25519 public key."""
    return {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": b64url(public)}


def load_signing_key(path):
    """This network's private key, from setup.py's keys.json; refused if others can read it."""
    path = pathlib.Path(path)
    if not path.is_file():
        raise FederationError(f"{path} does not exist; run `make keys` first")
    if path.stat().st_mode & 0o077:
        raise FederationError(
            f"{path} is readable by group or others; chmod 600 it before signing with it")
    try:
        raw = base64.b64decode(json.loads(path.read_text())["signingPrivate"], validate=True)
    except (ValueError, KeyError, binascii.Error) as err:
        raise FederationError(f"{path} holds no readable signingPrivate: {err}") from err
    if len(raw) != 32:
        raise FederationError(f"{path} signingPrivate is {len(raw)} bytes, want 32")
    return Ed25519PrivateKey.from_private_bytes(raw)


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


def validate_schema_packs(packs):
    """The schemaPacks list: distinct https URLs."""
    if not (isinstance(packs, list) and all(_is_https_url(p) for p in packs)
            and len(set(packs)) == len(packs)):
        raise FederationError("schema packs is not a list of distinct https URLs")
    return packs


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
    """A checked JWKS, as {kid: raw public key} for its Ed25519 keys. Duplicate kids are refused."""
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
    """The Network Descriptor contract, checked before signing and after fetching."""
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
    # Unsigned, so it must sit on the network's own domain.
    packs = descriptor.get("schemaPacks")
    need(_is_https_url(packs)
         and urllib.parse.urlsplit(packs).hostname == str(network_id).lower(),
         "schemaPacks is not an https URL on the network's own domain")
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


# --- sign and verify ------------------------------------------------------

def sign(descriptor, private, key_id):
    """A copy of the descriptor carrying its signature."""
    signed = {k: v for k, v in descriptor.items() if k != "signature"}
    signed["signature"] = {
        "keyId": key_id,
        "algorithm": ALGORITHM,
        "value": b64url(private.sign(canonical(signed))),
    }
    return signed


def verify(descriptor, keyset, now=None):
    """Check a descriptor's shape and signature against the JWKS. Proves origin, not trust."""
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

    try:
        Ed25519PublicKey.from_public_bytes(keys[key_id]).verify(
            unb64url(signature.get("value", "")), canonical(descriptor)
        )
    except Exception as err:
        raise FederationError("signature does not verify") from err

# --- fetching a peer's documents ------------------------------------------

class ResolvingHTTPSConnection(http.client.HTTPSConnection):
    """Like curl --resolve: connects to an address, still verifying the certificate's name."""

    def __init__(self, server_name, address, **kwargs):
        host, _, port = address.partition(":")
        super().__init__(host, int(port or 443), **kwargs)
        self._server_name = server_name

    def connect(self):
        super(http.client.HTTPSConnection, self).connect()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._server_name)


class Fetcher:
    """Retrieves and verifies another network's documents. scheme is https outside local tests."""

    def __init__(self, scheme="https", ca_file=None, resolve=None, timeout=10):
        self.scheme, self.resolve, self.timeout = scheme or "https", resolve, timeout
        # A different root CA, never verification turned off.
        self.context = ssl.create_default_context(cafile=ca_file) if ca_file else None

    def fetch(self, network_id):
        """Fetch a network's descriptor, JWKS and schema packs, verified against each other."""
        if not network_id:
            raise FederationError("no networkId given")

        descriptor = self._get(network_id, WELL_KNOWN_DESCRIPTOR)
        keyset = self._get(network_id, WELL_KNOWN_JWKS)

        if descriptor.get("networkId") != network_id:
            raise FederationError(
                f"{network_id} publishes a descriptor for {descriptor.get('networkId')!r}")
        verify(descriptor, keyset)
        where = urllib.parse.urlsplit(descriptor["schemaPacks"])
        served = self._get(network_id, where.path + (f"?{where.query}" if where.query else ""))
        if not isinstance(served, dict):
            raise FederationError(f"{network_id} serves schema packs that are not a JSON object")
        packs = validate_schema_packs(served.get("schemaPacks"))
        return descriptor, keyset, packs

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
            body = response.read(MAX_DOCUMENT_BYTES + 1)
            if len(body) > MAX_DOCUMENT_BYTES:
                raise FederationError(f"{where} is larger than {MAX_DOCUMENT_BYTES} bytes")
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


class ReadOnlyRegistry:
    """Registry searches, which need no credentials."""

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
        """The active schema pack URLs this deployment speaks."""
        rows = self._search("SchemaRegistry", {"status": {"eq": "active"}})
        return sorted({r["schemaUrl"] for r in rows if r.get("schemaUrl")})


def _registry_time(value):
    """A registry timestamp with any fraction length (Python < 3.11 reads 3 or 6). No zone is UTC."""
    text = re.sub(r"\.(\d+)", lambda m: "." + m.group(1)[:6].ljust(6, "0"),
                  str(value).replace("Z", "+00:00"), count=1)
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def registered_signing_keys(entry, now=None):
    """The entry's active, unexpired Ed25519 keys, including ones not valid yet."""
    now = now or datetime.now(timezone.utc)
    usable = []
    for k in entry.get("keys") or []:
        if str(k.get("alg", "")).lower() != "ed25519" or k.get("status", "active") != "active":
            continue
        name = k.get("osid", "?")
        until = k.get("validUntil")
        if until:
            try:
                expiry = _registry_time(until)
            except ValueError as err:
                raise FederationError(f"registry key {name} has an unreadable validUntil {until!r}") from err
            if expiry <= now:
                continue
        try:
            raw = base64.b64decode(str(k.get("key", "")).removeprefix("base64:"), validate=True)
        except binascii.Error as err:
            raise FederationError(f"registry key {name} is not base64") from err
        if len(raw) != 32:
            raise FederationError(f"registry key {name} is not a 32-byte Ed25519 key")
        if raw not in usable:
            usable.append(raw)
    return usable


def signing_key_osid(entry, public):
    """The osid of the entry's key holding this public half.

    The KEY's osid, not the entry's: a verifier walks a record's keys and
    matches on theirs, so this is the only id a signature may name.
    """
    for k in entry.get("keys") or []:
        held = str(k.get("key", "")).removeprefix("base64:")
        try:
            if base64.b64decode(held, validate=True) == public:
                return str(k.get("osid") or "")
        except binascii.Error:
            continue
    return ""


def last_published_revision(path):
    """The revision already on disk, or 0. Unreadable is an error, so a revision never goes back."""
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
    """Generate and sign this network's documents from its registry entry.

    --keys must hold the entry's active key: it signs both the descriptor and traffic.
    """
    out = pathlib.Path(args.out) / ".well-known"
    descriptor_path, jwks_path = out / "openagrinet", out / "jwks.json"
    packs_path = out / "schema-packs.json"

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

    registered = registered_signing_keys(entry)
    if not registered:
        raise FederationError(
            f"{args.network_id} has no active Ed25519 key in the registry to sign with")

    network_key = load_signing_key(args.keys)
    if public_of(network_key) not in registered:
        raise FederationError(
            f"{args.keys} is not an active key registered for {args.network_id}; "
            f"publishing it would sign the descriptor with one key and traffic with another")

    base_url = entry.get("baseUrl", "").rstrip("/")
    # The osid of the key we are about to sign with, so a verifier resolves the
    # same key from our record that we signed the descriptor with.
    key_id = signing_key_osid(entry, public_of(network_key))
    if not key_id:
        raise FederationError(f"{args.network_id}'s signing key has no osid in the registry")
    network_jwk = jwk(public_of(network_key), key_id)

    descriptor = {
        "version": DESCRIPTOR_VERSION,
        "networkId": entry["participantId"],
        "name": entry.get("name") or entry["participantId"],
        "operator": {"name": args.operator or entry.get("name") or entry["participantId"]},
        "revision": args.revision,
        "status": args.status,
        "issuedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "operatingProfile": [p.strip() for p in args.operating_profile.split(",") if p.strip()],
        "schemaPacks": f"https://{entry['participantId']}{WELL_KNOWN_SCHEMA_PACKS}",
        "services": {"discovery": f"{base_url}/discover"},
    }
    if args.projection_ttl is not None:
        descriptor["projectionTtl"] = args.projection_ttl

    packs = validate_schema_packs(registry.schema_packs())
    signed = sign(descriptor, network_key, key_id)
    keyset = {"keys": [network_jwk]}
    # Checked as a peer would, before anything is written.
    verify(signed, keyset)

    # Descriptor last, so it never names a key not yet published.
    out.mkdir(parents=True, exist_ok=True)
    write_atomically(packs_path, {"schemaPacks": packs})
    write_atomically(jwks_path, keyset)
    write_atomically(descriptor_path, signed)

    print(f"{args.network_id}: descriptor written at revision {args.revision}")
    print(f"  descriptor  {descriptor_path}")
    print(f"  keys        {jwks_path}")
    print(f"  packs       {packs_path} ({len(packs)})")
    print(f"  key         {network_jwk['kid']}")
    return 0


def cmd_verify(args):
    """Fetch another network's documents and check them."""
    descriptor, keyset, packs = fetcher_from(args).fetch(args.network_id)
    print(f"verified {descriptor['networkId']} at revision {descriptor.get('revision')}, "
          f"status {descriptor.get('status')}")
    print(f"  discovery  {(descriptor.get('services') or {}).get('discovery')}")
    print(f"  keys       {len(keyset.get('keys', []))} published")
    print(f"  packs      {len(packs)} at {descriptor['schemaPacks']}")
    for pack in packs:
        print(f"    {pack}")
    print("  these are what you may name in PACKS when you admit them as a source.")
    print("  this proves the documents are self-consistent, NOT that the network "
          "should be trusted -- admission is that decision")
    return 0


def fetcher_from(args):
    return Fetcher(scheme=args.scheme, ca_file=args.ca_file or None,
                   resolve=args.resolve or None, timeout=args.timeout)


# --- admission ------------------------------------------------------------

DIRECTIONS = ("source", "requester")
# A Sunbird RC record id. The peer is filed under it, so it must be one.
RECORD_ID = re.compile(r"1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class Registry(ReadOnlyRegistry):
    """Our registry, the one we write to."""

    def __init__(self, url, own_id, timeout=30):
        super().__init__(url, timeout)
        self.own_id = own_id

    def by_osid(self, osid):
        return self._search("Participant", {"osid": {"eq": osid}})

    def create(self, record):
        self._write("POST", "Participant", record)

    def update(self, record):
        body = {k: v for k, v in record.items() if not k.startswith("os")}
        self._write("PUT", f"Participant/{record['osid']}", body)

    def _write(self, method, path, body):
        request = urllib.request.Request(
            f"{self.url}/api/v1/{path}", method=method, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as err:
            raise FederationError(f"registry {method} {path} returned {err.code}: "
                                  f"{err.read().decode()[:200]}") from err
        except Exception as err:
            raise FederationError(f"reaching the registry at {self.url}: {err}") from err
        params = result.get("params", {}) if isinstance(result, dict) else {}
        if params.get("status") != "SUCCESSFUL":
            raise FederationError(f"registry {method} {path} failed: {params.get('errmsg', result)}")


def open_registry():
    """Our registry and our own id, from the environment when it carries both --
    a cluster Job has no .env -- otherwise from quick-start's setup.py."""
    url, own = os.environ.get("REGISTRY_URL"), os.environ.get("BECKN_NETWORK_ID")
    if url and own:
        return Registry(url, own)
    import setup
    setup.load_dotenv()
    return Registry(setup.registry_url(), setup.env("BECKN_NETWORK_ID"))


def peer_registry(network_id):
    """Our registry, refusing our own id: our entry is never a peer."""
    registry = open_registry()
    if network_id == registry.own_id:
        raise FederationError(f"{network_id} is this network, not a peer")
    return registry


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def peer_record(descriptor, keyset):
    """What the peer's own documents say about it, shaped as its entry in our registry.

    Only the key the descriptor's signature names is filed, under the kid itself:
    a verifier walks a record's keys matching on THEIR osid, so the kid has to
    land on the key rather than on the entry around it.
    """
    kid = descriptor["signature"]["keyId"]
    if not RECORD_ID.fullmatch(kid):
        raise FederationError(f"kid {kid!r} is not a registry record id (1-<uuid>); "
                              f"the peer cannot be filed under it")
    record = {"participantId": descriptor["networkId"], "name": descriptor["name"],
              "type": "node", "status": "active", "role": ["network"],
              "baseUrl": descriptor["services"]["discovery"],
              "keys": [{"osid": kid, "alg": "ed25519", "status": "active",
                        "validFrom": descriptor["issuedAt"],
                        "key": base64.b64encode(validate_keyset(keyset)[kid]).decode()}],
              "revision": descriptor["revision"]}
    if "projectionTtl" in descriptor:
        record["projectionTtl"] = descriptor["projectionTtl"]
    return kid, record


def file_peer(registry, descriptor, keyset):
    """Create the peer's entry, or bring its record up to date. Returns the stored entry.

    Never touches federationRole: a peer's documents cannot widen its admission.
    """
    kid, record = peer_record(descriptor, keyset)
    held = registry.participant(record["participantId"])
    if held is None:
        # Sunbird keeps a supplied osid only without its "1-" prefix, and only
        # on create: it prepends one itself, so passing the kid whole stores
        # 1-1-<uuid> and no signature naming the kid resolves.
        created = {**record, "keys": [{**record["keys"][0],
                                      "osid": kid.removeprefix("1-")}]}
        registry.create(created)
    else:
        if descriptor["revision"] < (held.get("revision") or 0):
            raise FederationError(
                f"revision {descriptor['revision']} is below the {held['revision']} we hold; "
                f"refusing a replay")
        same_key = [k.get("key") for k in held.get("keys") or []] == [record["keys"][0]["key"]]
        if descriptor["revision"] == held.get("revision") and not (
                same_key and all(held.get(f) == record.get(f) for f in ("baseUrl", "projectionTtl"))):
            raise FederationError(
                f"revision {descriptor['revision']} is the one we hold, with different contents")
        if same_key:
            record["keys"] = held["keys"]
        registry.update({**held, **record})
    return stored_as_written(registry, record["participantId"], kid, record["keys"][0]["key"])


def stored_as_written(registry, participant_id, kid, key):
    """Read the entry back and refuse one the registry did not store as asked.

    The kid must have landed on the KEY: a verifier resolves nothing without it,
    and the registry regenerates a nested osid that an update failed to echo."""
    stored = registry.participant(participant_id)
    if stored is None:
        raise FederationError(f"the registry did not store {participant_id}")
    if kid not in [k.get("osid") for k in stored.get("keys") or []]:
        raise FederationError(
            f"the registry did not store {participant_id}'s key as {kid}; "
            f"signatures naming it would resolve to no key")
    if [k.get("key") for k in stored.get("keys") or []] != [key]:
        raise FederationError(f"the registry did not store {participant_id}'s key")
    return stored


def our_schema_packs(out_dir):
    """The packs WE publish, from the document another network reads -- not our
    registry, which holds a row before anyone has been told about it."""
    path = pathlib.Path(out_dir) / ".well-known" / "schema-packs.json"
    if not path.is_file():
        raise FederationError(
            f"{path} does not exist, so this network publishes nothing to agree about.\n"
            f"  Run `make host-descriptor` first: a requester agreement covers the packs\n"
            f"  in the document the other network reads, not the rows in our registry.")
    try:
        served = json.loads(path.read_text()).get("schemaPacks")
    except json.JSONDecodeError as err:
        raise FederationError(f"parsing {path}: {err}") from err
    return validate_schema_packs(served)


def cmd_admit(args):
    """Admit a peer in one direction, after fetching and verifying its documents."""
    descriptor, keyset, published = fetcher_from(args).fetch(args.network_id)
    if descriptor["status"] != "Active":
        raise FederationError(f"{args.network_id} declares itself {descriptor['status']}")
    registry = peer_registry(args.network_id)
    # What can be agreed here, and both sides are a DOCUMENT: theirs when we
    # read from them, ours when they ask us.
    offered = published if args.direction == "source" else our_schema_packs(args.out)

    packs = sorted({p.strip() for p in args.schema_packs.split(",") if p.strip()})
    if packs == ["all"]:
        # A snapshot: what they publish TODAY. Re-run to take in a new pack,
        # because an agreement that silently widened would not be one.
        packs = sorted(offered)
        print(f"all: {len(packs)} pack(s) {args.network_id} publishes today")
    if not packs:
        whose = args.network_id if args.direction == "source" else "this network"
        lines = [f"name the packs this agreement covers, or `all` for every one of them.",
                 f"", f"  {whose} publishes {len(offered)}:"]
        lines += [f"    {p}" for p in sorted(offered)] or ["    (none)"]
        raise FederationError("\n".join(lines))
    outside = sorted(set(packs) - set(offered))
    if outside:
        whose = args.network_id if args.direction == "source" else "this network"
        raise FederationError(f"{whose} does not publish {', '.join(outside)}")

    entry = file_peer(registry, descriptor, keyset)
    roles = dict(entry.get("federationRole") or {})
    held = roles.get(args.direction) or {}
    now = now_utc()
    roles[args.direction] = {**held, "schemaPacks": packs, "descriptorRevision": descriptor["revision"],
                             "admittedAt": held.get("admittedAt", now), "updatedAt": now}
    registry.update({**entry, "federationRole": roles})
    print(f"admitted {args.network_id} as {args.direction} at revision {descriptor['revision']}, "
          f"covering {len(packs)} pack(s); filed under {entry['osid']}")
    return 0


def cmd_refresh(args):
    """Re-read an admitted peer's documents and update its facts. Its admission is unchanged."""
    registry = peer_registry(args.network_id)
    held = registry.participant(args.network_id)
    if held is None or not any(d in (held.get("federationRole") or {}) for d in DIRECTIONS):
        raise FederationError(f"{args.network_id} is not admitted; admit it first")
    descriptor, keyset, published = fetcher_from(args).fetch(args.network_id)
    entry = file_peer(registry, descriptor, keyset)
    roles = entry.get("federationRole") or {}
    print(f"refreshed {args.network_id} at revision {descriptor['revision']}")
    gone = sorted(set((roles.get("source") or {}).get("schemaPacks") or []) - set(published))
    if gone:
        print(f"  no longer published, still in our source agreement: {', '.join(gone)}")
    if descriptor["status"] != "Active":
        print(f"  it now declares itself {descriptor['status']}; suspend it if that ends the agreement")
    return 0


def cmd_suspend(args):
    """Withdraw one direction of a peer's admission. Re-admitting is the only way back."""
    registry = peer_registry(args.network_id)
    entry = registry.participant(args.network_id)
    roles = (entry or {}).get("federationRole") or {}
    if args.direction not in roles:
        raise FederationError(f"{args.network_id} is not admitted as {args.direction}")
    kept = {d: v for d, v in roles.items() if d in DIRECTIONS and d != args.direction}
    # The registry merges objects on update, so a direction is only dropped by
    # clearing all. That leaves a window with nothing admitted, and a failure
    # inside it has to say so rather than surface as the write's own error.
    registry.update({**entry, "federationRole": {}})
    try:
        if kept:
            registry.update({**registry.participant(args.network_id), "federationRole": kept})
        else:
            # Nothing agreed either way, so stop dealing with them at all: an inactive
            # participant is refused when its signature is checked, before any
            # federation rule is read. Admitting again sets it back to active.
            registry.update({**registry.participant(args.network_id), "status": "inactive"})
    except Exception as err:
        raise FederationError(
            f"{args.network_id} now has no admission at all: clearing it worked, "
            f"{'putting back ' + ', '.join(sorted(kept)) if kept else 'switching it off'} "
            f"did not.\n  Re-admit it in the directions you meant to keep.\n  {err}") from err
    left = (registry.participant(args.network_id).get("federationRole") or {})
    if args.direction in left:
        raise FederationError(f"the registry still holds {args.network_id} as {args.direction}")
    print(f"suspended {args.network_id} as {args.direction}"
          + ("" if kept else "; nothing is agreed either way, so it is now inactive"))
    return 0


# --- flags ----------------------------------------------------------------

def add_peer_access(parser):
    """How an operator reaches a peer's documents."""
    parser.add_argument("--scheme", default="https",
                        help="http only for a stack with no certificates at all")
    parser.add_argument("--ca-file", default="",
                        help="verify the peer's certificate against the CAs in this PEM file")
    parser.add_argument("--resolve", default="",
                        help="reach the peer at this host:port instead of resolving its domain")
    parser.add_argument("--timeout", type=float, default=10, help="per-request timeout")


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
    publish.add_argument("--keys", required=True,
                         help="the keys.json setup.py writes; its signing key signs the "
                              "descriptor AND the network's traffic, so it must be the "
                              "key registered on the entry")
    publish.set_defaults(run=cmd_publish)

    verify_cmd = commands.add_parser("verify", help="fetch and check another network's documents")
    verify_cmd.add_argument("--network-id", required=True, help="the peer to check")
    add_peer_access(verify_cmd)
    verify_cmd.set_defaults(run=cmd_verify)

    admit = commands.add_parser("admit", help="admit a peer network in one direction")
    admit.add_argument("--network-id", required=True, help="the peer, by its domain")
    admit.add_argument("--direction", required=True, choices=DIRECTIONS,
                       help="source: we read from it; requester: it may read from us")
    admit.add_argument("--out", default=".",
                       help="where host-descriptor wrote .well-known/. A requester "
                            "agreement covers the packs in that document, not the "
                            "rows in our registry")
    admit.add_argument("--schema-packs", default="",
                       help="comma-separated pack URLs the agreement covers")
    add_peer_access(admit)
    admit.set_defaults(run=cmd_admit)

    refresh = commands.add_parser("refresh", help="re-read an admitted peer's documents")
    refresh.add_argument("--network-id", required=True, help="the peer, by its domain")
    add_peer_access(refresh)
    refresh.set_defaults(run=cmd_refresh)

    suspend = commands.add_parser("suspend", help="withdraw one direction of a peer's admission")
    suspend.add_argument("--network-id", required=True, help="the peer, by its domain")
    suspend.add_argument("--direction", required=True, choices=DIRECTIONS)
    suspend.set_defaults(run=cmd_suspend)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except FederationError as err:
        print(f"oanfed {args.command}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
