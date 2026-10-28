#!/usr/bin/env python3
"""Prepare the stack, one step at a time.

    python3 bin/setup.py keys                    generate keys/keys.json
    python3 bin/setup.py register-network        our own entry in the registry
    python3 bin/setup.py register-provider NAME  one upstream and what it answers
    python3 bin/setup.py configure-adapter       render the adapter configs

Four commands rather than one run, because all four CREATE ONLY: a row that
already exists is left alone, never overwritten. So a wrong value is not fixed
by re-running -- it is fixed by a PUT against the stored row, the way oanfed.py
does it. Registering one provider at a time keeps that correction to one
provider rather than the whole network.

An id that was deleted still cannot be reused: delete is soft and keeps the
unique index.

WHAT EACH ONE WRITES

    keys               one ed25519 keypair for signing and one x25519 for
                       encryption, into keys/keys.json. The private halves
                       never leave this machine.
    register-network   1 node -- the network itself, BECKN_NETWORK_ID, shared
                       by all three adapters, carrying the public half.
    register-provider  1 upstream (the API this deployment calls -- it signs
                       nothing, so no role and no keys), 1 ProviderSchema row
                       binding it to a capability with the method, path and
                       mapping URL, and 1 SchemaRegistry row saying what that
                       capability MEANS. The schema row is per capability, not
                       per provider, so a second provider of one capability
                       shares it.
    configure-adapter  reads the bindings back OUT of the registry and renders
                       config/adapters/{multi-adapter,single-adapter}/*.yaml
                       from the .tmpl files beside them. A provider that was
                       never registered gets no block and answers nothing.

The binding keys are the load-bearing part. A provider step answers only when
the key it was configured with matches the one built from the incoming payload
-- and configure-adapter takes them from the registry rows themselves, so the
two cannot disagree.

The registry has no route through the gateway and publishes on loopback only,
which is why these writes live here rather than in a Postman request.

Safe to re-run. Keys are generated once and reused; a participant, binding or
schema already present is left alone rather than recreated.

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

    Nothing sources .env before this runs -- the make targets shell straight
    out to python3 -- and a real environment variable wins so a one-off
    override still works:

        REGISTRY_PORT=9081 python3 bin/setup.py register-network
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
    if is_placeholder(v):
        sys.exit(f"setup: {name} is still the .env.example placeholder {v!r}.\n"
                 f"{PLACEHOLDER_WHY}")
    return v


def is_placeholder(value):
    """True for a value still in .env.example's `<describe it here>` form.

    Every placeholder in the template is angle-bracketed, and no real base URL,
    path, credential or id can be -- so this is an exact test rather than a
    guess."""
    v = value.strip()
    return v.startswith("<") and v.endswith(">")


PLACEHOLDER_WHY = """\
  Fill it in in .env, then re-run. Nothing has been written yet.

  Refused rather than passed through: these commands create only, so a
  placeholder written now is not corrected by re-running."""


# Every provider this stack can serve, keyed by the name register-provider
# takes. method is the provider's contract: some answer a GET, some a POST.
PROVIDERS = {
    "mausamgram": {
        "prefix": "MAUSAMGRAM", "name": "IMD Mausamgram NWP",
        "capability_name": "Weather Observation", "version": "v0.1",
        "method": "GET", "default_path": "/get-daily",
        "needs": ["MAUSAMGRAM_AUTH"],
    },
    "agmarknet": {
        "prefix": "AGMARKNET", "name": "Agmarknet Vistaar",
        "capability_name": "Mandi Price", "version": "v0.1",
        "method": "GET", "default_path": "/v1/fetch-agmarknet-vistaar",
        "needs": ["AGMARKNET_TOKEN_URL", "AGMARKNET_ACCESS_NAME",
                  "AGMARKNET_PASSWORD"],
    },
    "vistaar": {
        "prefix": "VISTAAR", "name": "Bharat Vistaar knowledge retrieval",
        "capability_name": "Knowledge Advisory", "version": "v0.1",
        "method": "POST", "default_path": None,
        "needs": ["VISTAAR_TOKEN_URL", "VISTAAR_CLIENT_ID",
                  "VISTAAR_CLIENT_SECRET"],
    },
    "pocra": {
        "prefix": "POCRA", "name": "POCRA agriculture facilities",
        "capability_name": "Agriculture Facility", "version": "v0.1",
        "method": "POST", "default_path": None,
        "needs": [],
    },
}


def network_requirements():
    return ["BECKN_NETWORK_ID", "NETWORK_BASE_URL"]


def provider_requirements(name):
    """What this provider needs filled in before anything is written. Its
    credential variables are here too, so a placeholder is caught before the row."""
    p = PROVIDERS[name]
    keys = [f"{p['prefix']}_{s}" for s in
            ("PARTICIPANT_ID", "CAPABILITY", "BASE_URL", "MAPPING_URL")]
    if p["default_path"] is None:
        keys.append(f"{p['prefix']}_PATH")
    return keys + p["needs"]


def require(keys):
    """Refuse to touch the registry while any of these is unfilled."""
    missing = [k for k in keys if os.environ.get(k) is None]
    unfilled = [k for k in keys
                if os.environ.get(k) is not None
                and is_placeholder(os.environ[k])]
    if not missing and not unfilled:
        return
    lines = ["setup: .env is not ready, so nothing was written.\n"]
    if unfilled:
        lines.append("  Still the .env.example placeholder:")
        lines += [f"    {k}={os.environ[k]}" for k in unfilled]
    if missing:
        lines.append("  Absent from .env entirely:")
        lines += [f"    {k}" for k in missing]
    lines.append(PLACEHOLDER_WHY)
    sys.exit("\n".join(lines))


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
    """The network's one identity. Keys persist across runs."""
    participant = env("BECKN_NETWORK_ID")

    if KEYS.exists():
        print("keys: reusing keys/keys.json")
        identity = json.loads(KEYS.read_text())
        # The keypair persists; the id is read from .env each run. register-network
        # creates only, so changing the id seeds a NEW participant rather than
        # editing one -- and the same keypair moving to the new id is what makes
        # that a rename rather than a rekey.
        if identity["participantId"] != participant:
            print(f"  id is now {participant}, keeping the keypair")
            identity["participantId"] = participant
        KEYS.write_text(json.dumps(identity, indent=2))
        return identity

    print("keys: generating")
    sign_private, sign_public = ed25519_pair()
    encr_private, encr_public = x25519_pair()
    identity = {"participantId": participant,
                "signingPrivate": sign_private, "signingPublic": sign_public,
                "encrPrivate": encr_private, "encrPublic": encr_public}
    KEYS.parent.mkdir(exist_ok=True)
    KEYS.write_text(json.dumps(identity, indent=2))
    KEYS.chmod(0o600)
    return identity


# ----------------------------------------------------------------- registry

def registry_url():
    return f"http://localhost:{env('REGISTRY_PORT', '8081')}"


def post(entity, payload):
    # No {"EntityName": {...}} wrapper: this registry takes the record itself,
    # and a wrapper comes back as "extraneous key [...] is not permitted".
    req = urllib.request.Request(f"{registry_url()}/api/v1/{entity}", method="POST",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
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
            sys.exit(f"setup: the registry refused a write -- HTTP {e.code}: {detail}")


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

    No key id of our own: a sender names the entry's osid. No use either -- alg
    carries the purpose, ed25519 signs."""
    return [{"alg": "ed25519", "key": public_key, "status": "active",
             "validFrom": "2026-01-01T00:00:00Z", "validUntil": "2030-01-01T00:00:00Z"}]


def node(participant_id, name, role, base_url, public_key):
    """A participant that speaks Beckn. baseUrl is the address we publish, so
    it comes from .env -- see docs/adr/0002-filling-the-registry.md."""
    return {"participantId": participant_id, "name": name, "type": "node",
            "status": "active", "baseUrl": base_url,
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


def created(result, what):
    """Report a write, and stop on one the registry refused.

    These commands create only, so a refusal leaves a row absent rather than
    wrong -- and the first symptom further on is a crawl that collects nothing."""
    params = result.get("params", {}) if isinstance(result, dict) else {}
    if params.get("status") != "SUCCESSFUL":
        sys.exit(f"setup: the registry refused {what}: "
                 f"{params.get('errmsg', result)}\n"
                 f"  Nothing after this point was written.")
    print(f"  {what}: created")


def ensure_binding(participant_id, capability, path, mapping_url,
                   method="GET"):
    """Create a capability binding only when absent. The shape of actions,
    mappings and method is docs/adr/0002-filling-the-registry.md."""
    binding = f"{participant_id}|{capability}"
    if search("ProviderSchema", {"bindingKey": {"eq": binding}}):
        print(f"  {binding}: already present")
        return
    result = post("ProviderSchema", {
        "bindingKey": binding, "participantId": participant_id,
        "capabilityCode": capability, "status": "active",
        "actions": [{"action": "select", "method": method, "path": path,
                     "mappings": mapping_url,
                     "timeoutMs": 15000, "retryMax": 2,
                     "status": "active"}]})
    created(result, binding)


def schema_url(capability, version):
    """The pack URL for a capability code, derived so the two cannot drift.
    The host is the one catalogs carry in their @context."""
    return ("https://openagrinet.github.io/network-specs/"
            f"schema/{capability.partition(':')[2]}/{version}"
            "/attributes.yaml")


def ensure_capability_schema(capability, name, version):
    """Create a capability's schema row only when absent. A wrong schemaUrl is
    permanent for that code -- see docs/adr/0002-filling-the-registry.md."""
    if search("SchemaRegistry", {"capabilityCode": {"eq": capability}}):
        print(f"  {capability}: already present")
        return
    result = post("SchemaRegistry", {
        "capabilityCode": capability, "name": name, "version": version,
        "schemaUrl": schema_url(capability, version),
        "status": "active"})
    created(result, capability)


def ensure_participant(participant_id, payload):
    """Create only when absent. Delete here is soft and keeps the unique index,
    so a recreate would fail on a duplicate key rather than replacing."""
    if search("Participant", {"participantId": {"eq": participant_id}}):
        print(f"  {participant_id}: already present")
        return
    created(post("Participant", payload), participant_id)


def register_network(identity):
    """Our own entry: one node, shared by all three adapters. The registry is
    loopback-only, so this is the only place it can be written from."""
    wait_for_registry()
    print("registry: our network identity")
    ensure_participant(identity["participantId"],
                       node(identity["participantId"],
                            env("BECKN_NETWORK_NAME", identity["participantId"]),
                            list(ROLES), env("NETWORK_BASE_URL"),
                            identity["signingPublic"]))
    identity = key_osid(identity)
    KEYS.write_text(json.dumps(identity, indent=2))
    print(f"  signing key id {identity['keyId']}")


def register_provider(name):
    """One upstream, its binding and its schema. Three rows together: a provider
    with no binding answers nothing, and a binding with no schema resolves to
    no pack. A re-run with the same id changes nothing."""
    p = PROVIDERS[name]
    require(provider_requirements(name))
    wait_for_registry()

    participant = env(f"{p['prefix']}_PARTICIPANT_ID")
    capability = env(f"{p['prefix']}_CAPABILITY")

    print(f"registry: {name}")
    ensure_participant(participant,
                       upstream(participant, p["name"],
                                env(f"{p['prefix']}_BASE_URL")))
    ensure_binding(participant, capability,
                   env(f"{p['prefix']}_PATH", p["default_path"]),
                   env(f"{p['prefix']}_MAPPING_URL"),
                   method=p["method"])
    ensure_capability_schema(capability, p["capability_name"], p["version"])
    print(f"  binding key {participant}|{capability}")


def key_osid(identity):
    """Check the registry still holds the public half we have the private half
    for, and read back the KEY's osid: the key id the adapters sign with.

    The key's own osid, not the entry's. A verifier filters by participantId and
    then walks that record's keys matching on their osid, so a signature naming
    anything else resolves to no key and fails as an unregistered subscriber.

    The mismatch check matters because register-network creates only: a
    participant seeded against an earlier keys.json keeps that public key until
    someone rotates it deliberately. Signing with a new private half would
    produce signatures nobody can verify -- and the failure surfaces much later,
    as an authentication error with no obvious cause."""
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
            f"  This command creates only, so re-running will not republish the key.\n"
            f"  Either restore the matching keys/keys.json, rotate the registered key\n"
            f"  with a PUT that echoes its nested osid, or pick a new BECKN_NETWORK_ID\n"
            f"  in .env -- a deleted id cannot be reused.")
    identity["keyId"] = keys[0]["osid"]
    return identity


def registered_providers():
    """The providers the registry holds a binding for, carrying the key as the
    REGISTRY spells it -- so the adapter and the caller use the same string."""
    by_participant = {env(f"{p['prefix']}_PARTICIPANT_ID", ""): name
                      for name, p in PROVIDERS.items()}
    found = []
    for row in search("ProviderSchema", {"status": {"eq": "active"}}):
        name = by_participant.get(row["participantId"])
        if not name:
            print(f"  skipping {row['bindingKey']}: no provider in .env has "
                  f"participant id {row['participantId']}")
            continue
        found.append((name, row["bindingKey"], row["participantId"]))
    return sorted(found)


def provider_steps(providers):
    """The providerSteps entries and their names, one fragment per provider --
    the auth shape is the one thing the registry does not hold."""
    blocks, ids = [], []
    for name, binding, participant in providers:
        fragment = (ADAPTERS / "providers" / f"{name}.yaml.tmpl").read_text()
        blocks.append(fragment.replace("__BINDING_KEY__", binding)
                              .replace("__PARTICIPANT_ID__", participant))
        ids.append(f"        - {step_id(binding)}")
    return "".join(blocks).rstrip("\n"), "\n".join(ids)


def step_id(binding_key):
    """The plugin id a capability is served by: the capability code without its
    namespace, so openagrinet:WeatherObservation is served by WeatherObservation."""
    return binding_key.partition("|")[2].partition(":")[2]


# ------------------------------------------------------------------ configs

# Each role is a __ROLE_* placeholder prefix, a config filename, and a layer in
# the network entry's role list.
ROLES = ("consumer", "network", "provider")


def publish_url(folder):
    """Where a crawled catalog is handed in, which differs by mode.

    Single mode serves /publish from the same process; multi mode has it on the
    provider adapter, a container of its own."""
    if folder == "single-adapter":
        return "http://localhost:9201/publish"
    return "http://provider-adapter:9200/publish"


def configure_adapter(identity):
    wait_for_registry()
    providers = registered_providers()
    if not providers:
        sys.exit("setup: the registry holds no provider binding, so the adapter "
                 "would answer nothing.\n"
                 "  Register at least one first:\n"
                 "    make register-provider PROVIDER=mausamgram")
    print("configs: serving " + ", ".join(name for name, _, _ in providers))
    steps, step_ids = provider_steps(providers)

    # The three tier configs (multi-adapter/), then single-adapter/network-adapter,
    # which carries all three tiers' placeholders.
    for name in (*ROLES, "network-adapter"):
        folder = "single-adapter" if name == "network-adapter" else "multi-adapter"
        fname = name if name == "network-adapter" else f"{name}-adapter"
        template = (ADAPTERS / folder / f"{fname}.yaml.tmpl").read_text()
        # The provider blocks go in first: they carry placeholders of their own
        # that the shared pass below resolves.
        template = (template.replace("__PROVIDER_STEPS__", steps)
                            .replace("__PROVIDER_STEP_IDS__", step_ids))
        # All three adapters share the network's id and key.
        for role in (ROLES if name == "network-adapter" else (name,)):
            prefix = role.upper()
            for placeholder, value in (
                    (f"__{prefix}_SUBSCRIBER_ID__", identity["participantId"]),
                    (f"__{prefix}_KEY_ID__", identity["keyId"]),
                    (f"__{prefix}_SIGNING_PRIVATE__", identity["signingPrivate"]),
                    (f"__{prefix}_SIGNING_PUBLIC__", identity["signingPublic"]),
                    (f"__{prefix}_ENCR_PRIVATE__", identity["encrPrivate"]),
                    (f"__{prefix}_ENCR_PUBLIC__", identity["encrPublic"])):
                template = template.replace(placeholder, value)
        for placeholder, value in (
                # The token endpoints the provider fragments name. A deployment
                # fact, not a credential, so it is rendered directly -- the
                # access name and password stay as variable NAMES in the config
                # and reach the adapter through its environment.
                ("__AGMARKNET_TOKEN_URL__", env("AGMARKNET_TOKEN_URL", "")),
                ("__VISTAAR_TOKEN_URL__", env("VISTAAR_TOKEN_URL", "")),
                # The crawler hands a crawled catalog to /publish, which signs it
                # and forwards it on. /network/publish expects a signature the
                # crawl does not carry, and in multi mode /publish is elsewhere.
                ("__DISCOVERY_PUSH_URL__", publish_url(folder)),
                # on/off in .env, true/false in the config.
                ("__FEDERATION_ENABLED__",
                 "false" if env("FEDERATION_CRAWL_PEERS", "off").lower() == "off"
                 else "true"),
                ("__FEDERATION_REFRESH_SECONDS__", env("FEDERATION_REFRESH_SECONDS", "900")),
                ("__FEDERATION_PAGE_SIZE__", env("FEDERATION_PAGE_SIZE", "100")),
                ("__FEDERATION_INTENT__", env("FEDERATION_INTENT", "")),
                # Telemetry. One switch drives all three signals: with every
                # one false the plugin builds no exporter and never dials, so
                # a stack running without the observability profile stays
                # quiet instead of logging a refused connection on a loop.
                ("__OTEL_ENABLED__", env("ADAPTER_OTEL_ENABLED", "true")),
                ("__OTLP_ENDPOINT__", env("OTLP_ENDPOINT", "otel-collector:4317")),
                ("__OTEL_ENVIRONMENT__", env("OTEL_ENVIRONMENT", "dev"))):
            template = template.replace(placeholder, value)
        if "__" in template:
            sys.exit(f"setup: {fname}.yaml still has unrendered placeholders")
        out = ADAPTERS / folder / f"{fname}.yaml"
        # A bare `docker compose up -d` before this runs starts the adapters
        # too, and Docker creates a DIRECTORY at a bind-mount source that does
        # not exist. Writing would then fail with a bare IsADirectoryError that
        # says nothing about the cause.
        if out.is_dir():
            sys.exit(
                f"setup: {out} is a directory, not a file.\n"
                f"  Docker created it, which means the adapters were started before this\n"
                f"  ran. Bring them down, remove the empty directories and retry:\n"
                f"    docker compose down\n"
                f"    rmdir config/adapters/*/*.yaml\n"
                f"    make configure-adapter")
        out.write_text(template)
        out.chmod(0o600)  # holds a private key
        print(f"  config/adapters/{folder}/{fname}.yaml")


# ---------------------------------------------------------------- commands

def need_identity():
    """keys.json with the key id register-network read back into it."""
    if not KEYS.exists():
        sys.exit("setup: keys/keys.json does not exist -- run `make keys` first")
    identity = json.loads(KEYS.read_text())
    if not identity.get("keyId"):
        sys.exit("setup: keys/keys.json carries no key id, so nothing could sign.\n"
                 "  Run `make register-network` -- it reads the id back from the\n"
                 "  registry row it writes.")
    return identity


def main(argv):
    load_dotenv()
    command = argv[0] if argv else ""

    if command == "keys":
        require(["BECKN_NETWORK_ID"])
        load_or_generate_keys()
    elif command == "register-network":
        require(network_requirements())
        register_network(load_or_generate_keys())
    elif command == "register-provider":
        if len(argv) != 2 or argv[1] not in PROVIDERS:
            sys.exit("setup: register-provider takes one of "
                     + ", ".join(PROVIDERS))
        register_provider(argv[1])
    elif command == "configure-adapter":
        configure_adapter(need_identity())
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
