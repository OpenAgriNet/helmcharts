#!/usr/bin/env python3
"""Prepare the stack: generate the adapter keypairs, seed the registry, render
the adapter configs.

    python3 bin/setup.py

`make up` runs this as step 2, which is where it belongs -- the adapter configs
it renders are bind-mounted files, and an adapter started before they exist
leaves a directory in their place.

WHAT IT WRITES. Five participants and two capability bindings:

    3 x node      one per adapter -- exp, network, provider -- each with the
                  public halves of a keypair. The private halves stay in
                  keys/keys.json and never reach the registry.
    2 x upstream  the two APIs this deployment calls, addressed by compose
                  service name. An upstream signs nothing, so it needs no role
                  and no keys.
    2 x binding   a ProviderSchema row per capability: which upstream answers
                  it, the method and path, timeouts, and the mapping URL.

This is all of it. Nothing has to be created by hand afterwards, and nothing
can be from outside the VM -- the registry has no route through the gateway and
publishes on loopback only, which is why seeding lives here rather than in a
Postman request.

The binding keys are the load-bearing part. A provider step answers only when
the key it was configured with matches the one built from the incoming payload,
and both sides come from the same .env values in this one run -- which is what
keeps them from disagreeing. To point a capability somewhere else: edit .env,
re-run this, recreate the provider adapter.

Safe to re-run. Keys are generated once and reused from keys/keys.json, so the
identities already in the registry stay valid; participants and bindings that
exist are left alone rather than recreated, because this registry's delete is
soft and holds the unique index -- a deleted participantId cannot be reused.

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

    Nothing sources .env before this runs -- `make up` shells straight out to
    python3 -- and a real environment variable wins so a one-off override still
    works:

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
    """The published half of the signing keypair.

    Bare base64, with no encoding label in front of it: what a verifier hands
    to a base64 decoder is the value as published, and a label left on fails
    every verification with a decode error pointing nowhere near the registry.

    No friendly key id, because nothing could look one up: the registry assigns
    an osid on write and that is what a sender names in the Authorization
    header. No use either -- alg carries the purpose, ed25519 signs."""
    return [{"alg": "ed25519", "key": public_key, "status": "active",
             "validFrom": "2026-01-01T00:00:00Z", "validUntil": "2030-01-01T00:00:00Z"}]


def node(participant_id, name, role, public_key):
    """A participant that speaks Beckn.

    One level, no wrapper object: type decides which fields apply. baseUrl must
    be https for a node, and the id must be hostname-shaped -- it is the
    identity a signature is checked against. Neither is
    resolved here: routing between the adapters is the router plugin's config,
    which uses the compose service names."""
    return {"participantId": participant_id, "name": name, "type": "node",
            "status": "active", "baseUrl": f"https://{participant_id}",
            "role": role, "keys": signing_key_block(public_key)}


def upstream(participant_id, name, base_url):
    """An ordinary API the provider adapter calls.

    No role and no keys: it has never heard of Beckn, so it signs nothing and
    nothing verifies it. Both are permitted by the schema but neither is read --
    a signature is checked against the node identity that signed it.

    No credential either. The adapter presents one from its own config, naming
    the environment variable it comes from, so nothing secret is held here."""
    return {"participantId": participant_id, "name": name, "type": "upstream",
            "status": "active", "baseUrl": base_url}


def ensure_binding(bearer, participant_id, capability, path, mapping_url):
    """Create a capability binding only when absent.

    actions is a list, not a map: the registry treats every nested object as an
    entity and injects an osid into it, which a map cannot carry. It is also
    what lets one action be retired without touching the others.

    mappings is one reference carrying both directions, because the response
    mapping reads what the request mapping resolved."""
    binding = f"{participant_id}|{capability}"
    if search("ProviderSchema", {"bindingKey": {"eq": binding}}):
        print(f"  {binding}: already present")
        return
    result = post("ProviderSchema", {
        "bindingKey": binding, "participantId": participant_id,
        "capabilityCode": capability, "status": "active",
        "actions": [{"action": "select", "method": "GET", "path": path,
                     "mappings": mapping_url,
                     "timeoutMs": 15000, "retryMax": 2,
                     "status": "active"}]}, bearer)
    print(f"  {binding}: {result['params']['status']} "
          f"{result['params'].get('errmsg', '')[:160]}")


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

    # Five participants and two capability bindings, all of it from here.
    #
    # The registry is not reachable from outside this stack -- no published
    # port beyond loopback and no proxy host in front of it -- so there is no
    # second way to create these. Everything the network needs to answer a
    # request has to exist by the time this returns.
    print("registry: three adapter identities")
    for role, name, network_role in (
            ("exp", "OAN experience layer adapter", "consumer"),
            ("network", "OAN network layer adapter", "network"),
            ("provider", "OAN provider layer adapter", "provider")):
        identity = identities[role]
        ensure_participant(bearer, identity["participantId"],
                           node(identity["participantId"], name, network_role,
                                identity["signingPublic"]))

    # The two upstreams, addressed by compose service name: they are called from
    # inside this network and nowhere else.
    print("registry: two upstream providers")
    weather = env("PROVIDER_PARTICIPANT_ID")
    ensure_participant(bearer, weather,
                       upstream(weather, "IMD Mausamgram NWP (mock)",
                                env("MAUSAMGRAM_BASE_URL", "http://mockimd:9100")))
    mandi = env("MANDI_PARTICIPANT_ID")
    ensure_participant(bearer, mandi,
                       upstream(mandi, "Agmarknet Vistaar (mock)",
                                env("MANDI_BASE_URL", "http://mockagmarknet:9101")))

    # And what each of them answers. The binding key is participantId piped to
    # capabilityCode, and it has to match what the provider adapter was
    # rendered with -- both come from the same .env, which is what keeps them
    # from disagreeing.
    print("registry: two capability bindings")
    ensure_binding(bearer, weather, env("PROVIDER_CAPABILITY"),
                   env("MAUSAMGRAM_PATH", "/get-daily"), env("MAPPING_URL"))
    ensure_binding(bearer, mandi, env("MANDI_CAPABILITY"),
                   env("MANDI_PATH", "/v1/fetch-agmarknet-vistaar"),
                   env("MANDI_MAPPING_URL"))


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

        # Bare base64 now, but an older row may still carry the label, and a
        # confusing mismatch error is worse than one tolerant line.
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
    mandi_binding = f"{env('MANDI_PARTICIPANT_ID')}|{env('MANDI_CAPABILITY')}"
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
                ("__PROVIDER_BINDING_KEY__", binding),
                ("__MANDI_BINDING_KEY__", mandi_binding),
                # Telemetry. One switch drives all three signals: with every
                # one false the plugin builds no exporter and never dials, so
                # a stack running without the observability profile stays
                # quiet instead of logging a refused connection on a loop.
                ("__OTEL_ENABLED__", env("OTEL_ENABLED", "true")),
                ("__OTLP_ENDPOINT__", env("OTLP_ENDPOINT", "hyperdx:4317")),
                ("__OTEL_ENVIRONMENT__", env("OTEL_ENVIRONMENT", "dev"))):
            template = template.replace(placeholder, value)
        if "__" in template:
            sys.exit(f"setup: {role}.yaml still has unrendered placeholders")
        out = ADAPTERS / f"{role}.yaml"
        # A bare `docker compose up -d` before this script runs starts the
        # adapters too, and Docker creates a DIRECTORY at a bind-mount source
        # that does not exist. Writing would then fail with a bare
        # IsADirectoryError that says nothing about the cause.
        if out.is_dir():
            sys.exit(
                f"setup: {out} is a directory, not a file.\n"
                f"  Docker created it, which means the adapters were started before this\n"
                f"  script ran. Bring them down, remove the empty directories and retry:\n"
                f"    docker compose down\n"
                f"    rmdir config/adapters/*.yaml\n"
                f"    make up")
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
ready. The registry holds five participants and two capability bindings, and
the adapter configs are rendered, so nothing further has to be created by hand.

  {env('PROVIDER_PARTICIPANT_ID')}|{env('PROVIDER_CAPABILITY')}
  {env('MANDI_PARTICIPANT_ID')}|{env('MANDI_CAPABILITY')}

Those are the binding keys the provider adapter answers to. They were rendered
into its config from the same .env this seeded the registry from, which is what
keeps the two from disagreeing. A payload naming anything else is answered 404
"this module serves no capability matching the request" -- explicit, but it
names the request rather than the mismatch, so compare it against these two.

Both providers are mocks reached by compose service name. Pointing a capability
at a real upstream is an .env edit and a re-run of this: a new participant id
and base URL under PROVIDER_* or MANDI_*, which seeds a new Participant and
ProviderSchema row and re-renders the provider config so its binding key
matches. The registry is not reachable from outside this stack, so that write
happens from here.

Next: `make up` continues to step 3 and starts the adapters. If you ran this
on its own, the adapters need recreating to pick up the rendered configs:

  docker compose up -d --force-recreate provider-adapter network-adapter exp-adapter

Then import postman-collection/ and run it -- six requests, nothing to fill
in.""")
