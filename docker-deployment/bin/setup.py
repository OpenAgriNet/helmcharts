#!/usr/bin/env python3
"""Prepare the stack: generate the adapter keypairs, register the three adapter
identities, render the adapter configs.

    python3 bin/setup.py

Safe to re-run. Keys are generated once and reused from keys/keys.json, so the
identities already in the registry stay valid; participants that exist are left
alone rather than recreated, because this registry's delete is soft and holds
the unique index -- a deleted participantId cannot be reused.

WHAT THIS DOES NOT DO: it does not register the provider. The three rows here
are the adapters' own identities, which they need before they can sign anything
or verify each other. The upstream API is a Participant of type "upstream" plus
a ProviderSchema row, and its base URL belongs to whoever runs it -- so those
two are created by hand. README.md has the curl.

Needs python3 and the cryptography package:

    pip install cryptography
"""
import base64, json, os, pathlib, sys, time, urllib.error, urllib.parse, urllib.request

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization as ser

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEYS = ROOT / "keys" / "keys.json"
ADAPTERS = ROOT / "config" / "adapters"


def load_dotenv():
    """Read .env into the environment.

    There is no Makefile here to source it first, and a real environment
    variable wins so a one-off override still works:

        REGISTRY_PORT=9081 python3 bin/setup.py
    """
    path = ROOT / ".env"
    if not path.exists():
        sys.exit("setup: no .env -- copy .env.example to .env first")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip())


def env(name, default=None):
    v = os.environ.get(name, default)
    if v is None:
        sys.exit(f"setup: {name} is not set -- is it missing from .env?")
    return v


# --------------------------------------------------------------------- keys

def b64(raw):
    return base64.b64encode(raw).decode()


def ed25519_pair():
    k = Ed25519PrivateKey.generate()
    return (b64(k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())),
            b64(k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)))


def x25519_pair():
    k = X25519PrivateKey.generate()
    return (b64(k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())),
            b64(k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)))


def load_or_generate_keys():
    """Keys persist across runs: the registry already holds the public halves."""
    roles = (("exp", env("EXP_SUBSCRIBER_ID")),
             ("network", env("NETWORK_SUBSCRIBER_ID")),
             ("provider", env("PROVIDER_SUBSCRIBER_ID")))

    if KEYS.exists():
        print("keys: reusing keys/keys.json")
        identities = json.loads(KEYS.read_text())
        # The keypair persists; the id is read from .env each run. This registry
        # cannot update a record, so changing an id in .env seeds a NEW
        # participant rather than editing one -- and the same keypair moving to
        # the new id is what makes that a rename rather than a rekey.
        for role, participant in roles:
            if identities[role]["participantId"] != participant:
                print(f"  {role}: id is now {participant}, keeping the keypair")
                identities[role]["participantId"] = participant
        KEYS.write_text(json.dumps(identities, indent=2))
        return identities

    print("keys: generating")
    identities = {}
    for role, participant in roles:
        sign_private, sign_public = ed25519_pair()
        encr_private, encr_public = x25519_pair()
        identities[role] = {"participantId": participant,
                            "signingPrivate": sign_private, "signingPublic": sign_public,
                            "encrPrivate": encr_private, "encrPublic": encr_public}
    KEYS.parent.mkdir(exist_ok=True)
    KEYS.write_text(json.dumps(identities, indent=2))
    KEYS.chmod(0o600)
    return identities


# ----------------------------------------------------------------- registry

def registry_url():
    return f"http://localhost:{env('REGISTRY_PORT', '8081')}"


def token():
    body = urllib.parse.urlencode({
        "client_id": env("KEYCLOAK_CLIENT_ID", "registry-frontend"),
        "grant_type": "password",
        "username": env("REGISTRY_USER", "no-user"),
        "password": env("REGISTRY_PASSWORD", "no-user-password"),
    }).encode()
    # Keycloak sits behind PROXY_ADDRESS_FORWARDING, so it builds the token's
    # issuer from these headers. Without them it answers with an empty body.
    #
    # keycloak:8080 is the CONTAINER-INTERNAL address, and is deliberately not
    # KEYCLOAK_PORT. The registry validates the issuer against
    # OAUTH2_RESOURCES_0_URI, which names that internal address -- so a token
    # minted with the host port in its issuer is rejected with a 401 and an
    # empty body, however the port is published.
    req = urllib.request.Request(
        f"http://localhost:{env('KEYCLOAK_PORT', '8080')}/auth/realms/"
        f"{env('KEYCLOAK_REALM', 'sunbird-rc')}/protocol/openid-connect/token",
        data=body, headers={"X-Forwarded-Host": "keycloak:8080",
                            "X-Forwarded-Proto": "http"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if not payload.get("access_token"):
        sys.exit("setup: keycloak issued no token -- check the KEYCLOAK_* values in .env")
    return payload["access_token"]


def post(entity, payload, bearer):
    # No {"EntityName": {...}} wrapper: this registry takes the record itself,
    # and a wrapper comes back as "extraneous key [...] is not permitted".
    req = urllib.request.Request(f"{registry_url()}/api/v1/{entity}", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {bearer}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # The registry answers a rejected write with a JSON envelope, but not
        # always: a 401 comes back with an empty body. Decoding blindly turns
        # that into a JSONDecodeError traceback that says nothing about what
        # went wrong, so report the status instead.
        raw = e.read()
        try:
            return json.loads(raw)
        except ValueError:
            detail = raw.decode(errors="replace").strip()[:200] or "(empty body)"
            sys.exit(f"setup: the registry refused a write -- HTTP {e.code}: {detail}\n"
                     f"  A 401 here usually means the token was minted for a different\n"
                     f"  issuer than the registry validates against.")


def search(entity, filters):
    req = urllib.request.Request(f"{registry_url()}/api/v1/{entity}/search", method="POST",
                                 data=json.dumps({"filters": filters}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("data", [])


def wait_for_registry():
    for _ in range(60):
        try:
            search("Participant", {})
            return
        except Exception:
            time.sleep(2)
    sys.exit("setup: the registry did not come up -- check `docker compose ps`\n"
             "  and that REGISTRY_PORT in .env matches the published port")


def signing_key_block(public_key):
    # The base64: label is the registry's own encoding marker; the adapter
    # strips it before the value reaches signature validation.
    return [{"keyId": "k1", "use": "sign", "alg": "ed25519",
             "key": f"base64:{public_key}", "status": "active",
             "validFrom": "2026-01-01T00:00:00Z", "validUntil": "2030-01-01T00:00:00Z"}]


def node(participant_id, name, role, public_key):
    """A participant that speaks Beckn.

    One level, no wrapper object: type decides which fields apply. baseUrl must
    be https for a node, and the id must be hostname-shaped -- it is the
    identity that goes on the wire as context.bapId / bppId. Neither is
    resolved here: routing between the adapters is the router plugin's config,
    which uses the compose service names."""
    return {"participantId": participant_id, "name": name, "type": "node",
            "status": "active", "baseUrl": f"https://{participant_id}/beckn",
            "role": role, "keys": signing_key_block(public_key)}


def ensure_participant(bearer, participant_id, payload):
    """Create only when absent. Delete here is soft and keeps the unique index,
    so a recreate would fail on a duplicate key rather than replacing."""
    if search("Participant", {"participantId": {"eq": participant_id}}):
        print(f"  {participant_id}: already present")
        return
    result = post("Participant", payload, bearer)
    status = result["params"]["status"]
    print(f"  {participant_id}: {status} {result['params'].get('errmsg', '')[:160]}")


def seed(identities):
    wait_for_registry()
    bearer = token()
    print("registry: the three adapter identities")

    for role, name, beckn_role in (
            ("exp", "OAN experience layer adapter", "BAP"),
            ("network", "OAN network layer adapter", "NETWORK"),
            ("provider", "OAN provider layer adapter", "BPP")):
        identity = identities[role]
        ensure_participant(bearer, identity["participantId"],
                           node(identity["participantId"], name, beckn_role,
                                identity["signingPublic"]))


def key_osids(identities):
    """Read back each key's osid, and check the registry still holds the public
    half we have the private half for.

    The Authorization header names a key by its osid rather than by the friendly
    id, so the adapters have to be configured with the value the registry
    assigned.

    The mismatch check matters because this registry cannot update a record and
    its delete is soft: a participant seeded against an earlier keys.json keeps
    that public key forever. Signing with a new private half would then produce
    signatures nobody can verify -- and the failure would surface much later, as
    an authentication error with no obvious cause."""
    for role, identity in identities.items():
        records = search("Participant", {"participantId": {"eq": identity["participantId"]}})
        keys = (records[0].get("keys") or []) if records else []
        if not keys:
            sys.exit(f"setup: {identity['participantId']} has no published key")

        published = keys[0]["key"].removeprefix("base64:")
        if published != identity["signingPublic"]:
            sys.exit(
                f"setup: {identity['participantId']} is registered with a different key.\n"
                f"  This registry cannot update a record, and its delete is soft and keeps\n"
                f"  the unique index, so the id cannot be reused. Either restore the\n"
                f"  matching keys/keys.json, or pick a new id for {role.upper()}_SUBSCRIBER_ID\n"
                f"  in .env and re-run.")
        identity["keyOsid"] = keys[0]["osid"]
    return identities


# ------------------------------------------------------------------ configs

def render(identities):
    print("configs:")
    binding = f"{env('PROVIDER_PARTICIPANT_ID')}|{env('PROVIDER_CAPABILITY')}"
    for role in ("exp", "network", "provider"):
        identity = identities[role]
        template = (ADAPTERS / f"{role}.yaml.tmpl").read_text()
        prefix = role.upper()
        for placeholder, value in (
                (f"__{prefix}_SUBSCRIBER_ID__", identity["participantId"]),
                (f"__{prefix}_KEY_ID__", identity["keyOsid"]),
                (f"__{prefix}_SIGNING_PRIVATE__", identity["signingPrivate"]),
                (f"__{prefix}_SIGNING_PUBLIC__", identity["signingPublic"]),
                (f"__{prefix}_ENCR_PRIVATE__", identity["encrPrivate"]),
                (f"__{prefix}_ENCR_PUBLIC__", identity["encrPublic"]),
                ("__PROVIDER_BINDING_KEY__", binding)):
            template = template.replace(placeholder, value)
        if "__" in template:
            sys.exit(f"setup: {role}.yaml still has unrendered placeholders")
        out = ADAPTERS / f"{role}.yaml"
        out.write_text(template)
        out.chmod(0o600)  # holds a private key
        print(f"  config/adapters/{role}.yaml")


if __name__ == "__main__":
    load_dotenv()
    identities = load_or_generate_keys()
    seed(identities)
    identities = key_osids(identities)
    KEYS.write_text(json.dumps(identities, indent=2))
    render(identities)
    print(f"""
ready -- the adapters can now sign and verify each other.

Still to do by hand, because the base URL is not this stack's to know:

  1. give the upstream API a URL this VM can reach. Tunnelled from a laptop,
     that is: ngrok http 9100
  2. register it -- two rows, see README.md:
       Participant     type "upstream", baseUrl = that https URL
       ProviderSchema  bindingKey {env('PROVIDER_PARTICIPANT_ID')}|{env('PROVIDER_CAPABILITY')}
  3. docker compose up -d

The bindingKey above is what the provider adapter was just configured to
answer to. If the ProviderSchema row says anything else, the adapter concludes
the request is not its own and passes it through -- and the reply is a bare
ACK with no on_select, which looks like nothing happened.""")
