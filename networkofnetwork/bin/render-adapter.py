#!/usr/bin/env python3
"""Render this network's ONE adapter config.

quick-start's setup.py does everything that is hard and shared -- generating the
keypair, seeding the registry, registering the schemas -- and is reused as-is.
What it cannot do is render a config it has never heard of, so this does that
one step and nothing else.

It reads what setup.py already wrote: the network's keys.json and its .env. No
value is invented here.

Run:  render-adapter.py <networkId>
"""
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = ROOT / "config" / "adapter.yaml.tmpl"
STATE = ROOT / "networks"


def read_env(path):
    """The network's .env, as a dict. Same shape setup.py reads."""
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: render-adapter.py <networkId>")
    network = argv[1].strip()
    directory = STATE / network

    keys_file = directory / "keys" / "keys.json"
    env_file = directory / ".env"
    for required in (keys_file, env_file):
        if not required.exists():
            sys.exit(f"render-adapter: {required} is missing -- run setup first")

    env = read_env(env_file)
    # One identity for the whole network, so any role's entry carries it. The
    # file is still keyed by role because setup.py writes it that way.
    identity = json.loads(keys_file.read_text())["network"]

    def value(name, default=None):
        got = env.get(name, default)
        if got is None:
            sys.exit(f"render-adapter: {name} is not set in {env_file}")
        return got

    def binding(prefix):
        return f"{value(prefix + '_PARTICIPANT_ID')}|{value(prefix + '_CAPABILITY')}"

    substitutions = (
        ("__BECKN_NETWORK_ID__", identity["participantId"]),
        ("__KEY_ID__", identity["keyOsid"]),
        ("__SIGNING_PRIVATE__", identity["signingPrivate"]),
        ("__SIGNING_PUBLIC__", identity["signingPublic"]),
        ("__ENCR_PRIVATE__", identity["encrPrivate"]),
        ("__ENCR_PUBLIC__", identity["encrPublic"]),
        ("__NETWORK_BASE_URL__", value("NETWORK_BASE_URL")),
        ("__MAUSAMGRAM_BINDING_KEY__", binding("MAUSAMGRAM")),
        ("__AGMARKNET_BINDING_KEY__", binding("AGMARKNET")),
        ("__VISTAAR_BINDING_KEY__", binding("VISTAAR")),
        ("__POCRA_BINDING_KEY__", binding("POCRA")),
        ("__MAUSAMGRAM_PARTICIPANT_ID__", value("MAUSAMGRAM_PARTICIPANT_ID")),
        ("__AGMARKNET_PARTICIPANT_ID__", value("AGMARKNET_PARTICIPANT_ID")),
        ("__VISTAAR_PARTICIPANT_ID__", value("VISTAAR_PARTICIPANT_ID")),
        ("__POCRA_PARTICIPANT_ID__", value("POCRA_PARTICIPANT_ID")),
        ("__AGMARKNET_TOKEN_URL__", value("AGMARKNET_TOKEN_URL")),
        ("__VISTAAR_TOKEN_URL__", value("VISTAAR_TOKEN_URL")),
        # Empty disables peer crawling: the crawler builds no peer half without
        # a network id, so /crawl/peers refuses and the refresh loop never
        # starts. Answering another network's crawl is unaffected.
        ("__FEDERATION_NETWORK_ID__",
         "" if value("FEDERATION_CRAWL_PEERS", "on").lower() == "off"
         else value("BECKN_NETWORK_ID")),
        ("__FEDERATION_REFRESH_SECONDS__", value("FEDERATION_REFRESH_SECONDS", "900")),
        ("__FEDERATION_PAGE_SIZE__", value("FEDERATION_PAGE_SIZE", "0")),
        ("__FEDERATION_UPDATE_MODE__", value("FEDERATION_UPDATE_MODE", "MERGE")),
        ("__FEDERATION_INTENT__", value("FEDERATION_INTENT", "")),
        ("__FEDERATION_PUBLISH_RETRY_SECONDS__", value("FEDERATION_PUBLISH_RETRY_SECONDS", "30")),
        ("__OTEL_ENABLED__", value("ADAPTER_OTEL_ENABLED", "false")),
        ("__OTLP_ENDPOINT__", value("OTLP_ENDPOINT", "hyperdx:4317")),
        ("__OTEL_ENVIRONMENT__", value("OTEL_ENVIRONMENT", "dev")),
    )

    rendered = TEMPLATE.read_text()
    for placeholder, replacement in substitutions:
        rendered = rendered.replace(placeholder, replacement)

    # A placeholder left behind is a value nobody noticed was missing, and the
    # adapter would start with "__SIGNING_PRIVATE__" as a key.
    if "__" in rendered:
        leftover = sorted({w for w in rendered.split() if w.startswith("__")})
        sys.exit(f"render-adapter: unrendered placeholders remain: {leftover}")

    out_dir = directory / "config"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "adapter.yaml"
    if out.is_dir():
        sys.exit(f"render-adapter: {out} is a directory -- docker created it by "
                 f"starting the adapter before this ran. Remove it and retry.")
    out.write_text(rendered)
    out.chmod(0o600)  # holds a private key
    print(f"render-adapter: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
