#!/usr/bin/env python3
"""oanfed -- publish and verify a network's federation documents.

Served at https://{networkId}/.well-known/: openagrinet (signed descriptor),
jwks.json and schema-packs.json, all generated from our registry entry.

    oanfed.py publish    generate and sign this network's documents
    oanfed.py verify     fetch a network's documents and check them
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
# The version stack this build speaks.
OPERATING_PROFILE = ["OAN/v0.1", "Beckn/v2"]
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
        raise FederationError(f"{path} does not exist; run setup.py first")
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
    """The entry's active, unexpired Ed25519 keys, including ones not valid yet, each to its osid."""
    now = now or datetime.now(timezone.utc)
    usable = {}
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
        usable.setdefault(raw, k.get("osid"))
    return usable


class Registry(ReadOnlyRegistry):
    """Our registry, with the admin token writes need."""

    def __init__(self, url, token, own_id, timeout=30):
        super().__init__(url, timeout)
        self.token, self.own_id = token, own_id

    def update(self, record):
        body = {k: v for k, v in record.items() if not k.startswith("os")}
        self._write("PUT", f"Participant/{record['osid']}", body)

    def _write(self, method, path, body):
        request = urllib.request.Request(
            f"{self.url}/api/v1/{path}", method=method, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
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
    """Our registry, signed in with the admin from .env, as setup.py does."""
    import setup
    setup.load_dotenv()
    return Registry(setup.registry_url(), setup.token(), setup.env("BECKN_NETWORK_ID"))


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
    The revision lives on the entry, so losing .well-known/ never sends it backwards.
    """
    out = pathlib.Path(args.out) / ".well-known"
    descriptor_path, jwks_path = out / "openagrinet", out / "jwks.json"
    packs_path = out / "schema-packs.json"

    registry = open_registry()
    network_id = registry.own_id
    entry = registry.participant(network_id)
    if entry is None:
        raise FederationError(
            f"{network_id} is not in this registry -- seed it first, then publish from it")

    operator = os.environ.get("NETWORK_OPERATOR_NAME", "").strip()
    if not operator or operator.startswith("<"):
        raise FederationError(
            "NETWORK_OPERATOR_NAME is not set in .env -- name the organisation "
            "responsible for this network")
    ttl = os.environ.get("FEDERATION_PROJECTION_TTL", "").strip()

    registered = registered_signing_keys(entry)
    if not registered:
        raise FederationError(
            f"{network_id} has no active Ed25519 key in the registry to sign with")
    unnamed = [b64url(raw) for raw, osid in registered.items() if not osid]
    if unnamed:
        raise FederationError(f"{network_id}'s registry key {unnamed[0]} has no osid")

    network_key = load_signing_key(args.keys)
    if public_of(network_key) not in registered:
        raise FederationError(
            f"{args.keys} is not an active key registered for {network_id}; "
            f"publishing it would sign the descriptor with one key and traffic with another")
    key_id = registered[public_of(network_key)]

    # The disk copy is read too, so a stack that published before the registry
    # held the revision keeps counting up from it.
    revision = max(entry.get("revision") or 0, last_published_revision(descriptor_path)) + 1

    base_url = entry.get("baseUrl", "").rstrip("/")
    descriptor = {
        "version": DESCRIPTOR_VERSION,
        "networkId": entry["participantId"],
        "name": entry.get("name") or entry["participantId"],
        "operator": {"name": operator},
        "revision": revision,
        "status": args.status,
        "issuedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "operatingProfile": args.operating_profile or OPERATING_PROFILE,
        "schemaPacks": f"https://{entry['participantId']}{WELL_KNOWN_SCHEMA_PACKS}",
        "services": {"discovery": f"{base_url}/discover"},
    }
    if ttl:
        try:
            descriptor["projectionTtl"] = int(ttl)
        except ValueError as err:
            raise FederationError(f"FEDERATION_PROJECTION_TTL {ttl!r} is not a number") from err

    packs = validate_schema_packs(registry.schema_packs())
    signed = sign(descriptor, network_key, key_id)
    # Every usable key, so a peer still holding the previous one verifies through a rotation.
    keyset = {"keys": [jwk(raw, osid) for raw, osid in registered.items()]}
    # Checked as a peer would, before anything is written.
    verify(signed, keyset)

    # Recorded before publishing: a failure after this skips a revision, never repeats one.
    registry.update({**entry, "revision": revision})

    # Descriptor last, so it never names a key not yet published.
    out.mkdir(parents=True, exist_ok=True)
    write_atomically(packs_path, {"schemaPacks": packs})
    write_atomically(jwks_path, keyset)
    write_atomically(descriptor_path, signed)

    print(f"{network_id}: descriptor written at revision {revision}")
    print(f"  descriptor  {descriptor_path}")
    print(f"  keys        {jwks_path} ({len(keyset['keys'])})")
    print(f"  packs       {packs_path} ({len(packs)})")
    print(f"  signed by   {key_id}")
    return 0


def cmd_verify(args):
    """Fetch another network's documents and check them."""
    descriptor, keyset, packs = fetcher_from(args).fetch(args.network_id)
    print(f"verified {descriptor['networkId']} at revision {descriptor.get('revision')}, "
          f"status {descriptor.get('status')}")
    print(f"  discovery  {(descriptor.get('services') or {}).get('discovery')}")
    print(f"  keys       {len(keyset.get('keys', []))} published")
    print(f"  packs      {len(packs)} at {descriptor['schemaPacks']}")
    print("  this proves the documents are self-consistent, NOT that the network "
          "should be trusted -- admission is that decision")
    return 0


def fetcher_from(args):
    # Only verify takes --scheme: anything that writes a peer into the registry is https.
    return Fetcher(scheme=getattr(args, "scheme", "https"), ca_file=args.ca_file or None,
                   resolve=args.resolve or None, timeout=args.timeout)


# --- flags ----------------------------------------------------------------

def add_peer_access(parser):
    """How an operator reaches a peer's documents."""
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
    publish.add_argument("--operating-profile", action="append",
                         help="one version in the stack; repeat for each "
                              f"(default {' '.join(OPERATING_PROFILE)})")
    publish.add_argument("--status", default="Active", choices=DESCRIPTOR_STATUSES,
                         help="what this network declares itself to be")
    publish.add_argument("--out", required=True, help="directory to write .well-known/ into")
    publish.add_argument("--keys", required=True,
                         help="the keys.json setup.py writes; its signing key signs the "
                              "descriptor AND the network's traffic, so it must be the "
                              "key registered on the entry")
    publish.set_defaults(run=cmd_publish)

    verify_cmd = commands.add_parser("verify", help="fetch and check another network's documents")
    verify_cmd.add_argument("--network-id", required=True, help="the peer to check")
    verify_cmd.add_argument("--scheme", default="https",
                            help="http only for a stack with no certificates at all")
    add_peer_access(verify_cmd)
    verify_cmd.set_defaults(run=cmd_verify)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except FederationError as err:
        print(f"oanfed {args.command}: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
