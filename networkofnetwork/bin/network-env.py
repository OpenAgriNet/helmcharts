#!/usr/bin/env python3
"""Write one network's .env, from quick-start/.env.example.

Every network runs the SAME docker-compose.network.yml. What differs between
them is this file: the network's identity, the host ports it publishes, and
where its own state lives. Everything else -- images, the spec URL, the mapping
URLs -- is read from the example rather than restated here, so this does not
drift when the example gains a variable.

Ports come from bin/network-ports.py, which allocates a block per network and
remembers it. Nothing here picks a port.

Upstream capabilities point at this network's own mock servers. The federation
demo never calls them; they exist because bin/setup.py refuses to seed the
registry while any required value is still a placeholder. No real credential is
used, and none belongs in a file like this.

Run:  network-env.py <networkId>
"""
import pathlib
import socket
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
EXAMPLE = ROOT / ".env.example"
# Generated per-network state, in one place and gitignored as a whole -- .env,
# keys, TLS material and the rendered adapter config all carry secrets.
STATE = ROOT / "networks"

# Every value whose placeholder would stop setup.py's preflight. None is secret.
#
# They must be https: the registry's Participant schema pins baseUrl to
# ^https://..., so an http:// value is refused at seeding time with a validation
# error naming the pattern. That is why these are hostnames under the network
# rather than the "http://mock-imd:9100" the containers actually answer on --
# the registry record is metadata, and this demo never invokes these upstreams.
MOCK_CREDENTIALS = {
    "MAUSAMGRAM_BASE_URL": "https://mausamgram.{network}",
    # The path the MOCK serves, not IMD's real /nwpapi/get-daily. The front door
    # answers this hostname and proxies to mock-imd, so the path has to be the
    # mock's or the call 404s after everything else worked.
    "MAUSAMGRAM_PATH": "/get-daily",
    "MAUSAMGRAM_AUTH": "Basic bW9jazptb2Nr",
    "AGMARKNET_BASE_URL": "https://agmarknet.{network}",
    "AGMARKNET_TOKEN_URL": "https://agmarknet.{network}/token",
    "AGMARKNET_ACCESS_NAME": "mock",
    "AGMARKNET_PASSWORD": "mock",
    "VISTAAR_BASE_URL": "https://vistaar.{network}",
    "VISTAAR_TOKEN_URL": "https://vistaar.{network}/token",
    "VISTAAR_CLIENT_ID": "mock",
    "VISTAAR_CLIENT_SECRET": "mock",
    "POCRA_BASE_URL": "https://pocra.{network}",
}

# Upstream participant ids are per network: two networks seeding the same
# registry ids would be two different participants with one name the moment
# anything looked across them.
UPSTREAM_IDS = ("MAUSAMGRAM_PARTICIPANT_ID", "AGMARKNET_PARTICIPANT_ID",
                "VISTAAR_PARTICIPANT_ID", "POCRA_PARTICIPANT_ID")


def read_example():
    """The example's keys in order, and their values."""
    if not EXAMPLE.exists():
        sys.exit(f"network-env: {EXAMPLE} is missing")
    order, values = [], {}
    for line in EXAMPLE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        order.append(key)
        values[key] = value.strip()
    return order, values


def ports_for(network):
    """This network's allocated block, as the variables it fills."""
    out = subprocess.run([sys.executable, str(HERE / "network-ports.py"), "ports", network],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if line)


def resolve(values, network):
    """One network's values: the example, with what must differ overridden."""
    out = dict(values)
    out.update({k: v.format(network=network) for k, v in MOCK_CREDENTIALS.items()})
    out.update(ports_for(network))

    # The identity. ONE participant: the network IS the party, however many
    # layers it serves, so there are no per-role subscriber ids.
    out["BECKN_NETWORK_ID"] = network
    out["DISCOVERY_SUBSCRIBER_ID"] = f"discovery.{network}"

    # The address another network reaches this one at, and what the descriptor
    # publishes as services.discovery.
    #
    # It ends in /discover, and that is not decoration: the adapter takes the
    # ACTION FROM THE URL -- the router strips the module's mount path and
    # routes on what is left. A base url ending at /network arrives as an empty
    # action and is refused with "endpoint '' is not supported".
    #
    # https and a hostname because the Participant schema refuses anything else.
    # Inside the stack the modules reach each other by localhost, which their
    # own config carries.
    out["NETWORK_BASE_URL"] = f"https://{network}/network/discover"

    # Where this network's own state lives. Read by docker-compose.network.yml
    # for its bind mounts, and by setup.py for keys and the rendered config.
    out["NETWORK_DIR"] = str(STATE / network)

    # The crawl's own settings. Defaults rather than questions: a demo that
    # asked for a page size before it would start would be asking about the one
    # thing it is least likely to need changed.
    # The UPPER bound on how often peers are re-crawled. A peer's own
    # projectionTtl can shorten it, but never lengthen it -- and the interval is
    # recomputed from the peers found on each pass, so a deployment that had NO
    # peers when it started waits this long before looking again. 900 meant a
    # network admitted after startup was not crawled for fifteen minutes.
    out["FEDERATION_REFRESH_SECONDS"] = "30"
    out["FEDERATION_PAGE_SIZE"] = "0"
    out["FEDERATION_UPDATE_MODE"] = "MERGE"
    # How long a peer may serve a copy of our catalogues. The crawler refreshes
    # at projectionTtl/3 floored at 30s, so 30 means a peer re-crawls us every
    # 30 seconds -- short enough to watch a catalog cross during a demo.
    out["FEDERATION_PROJECTION_TTL"] = "30"
    # What breadth of a peer's catalogue to mirror: all of it.
    #
    # The star does the work. Discovery will not take a bare selector -- it
    # wants a test, not a projection -- and it refuses a test that narrows
    # nothing, so exists(), like_regex, "starts with" and != are all out. But a
    # star with ONE equality is fine, and isActive is the one field every
    # catalog has.
    #
    # Deliberately not a list of the capabilities we speak: that would be a
    # fifth place the capability set is written, and adding a sixth capability
    # would silently stop it being mirrored.
    #
    # Broad here does not mean mirroring a peer's whole index: context.networkId
    # and schemaContext still narrow what comes back.
    out["FEDERATION_INTENT"] = '{"filters": {"type": "jsonpath", "expression": "$.catalogs[*] ? (@.isActive == true)"}}'
    out["FEDERATION_PUBLISH_RETRY_SECONDS"] = "30"

    # No collector runs here, and discovery REFUSES to start with an exporter
    # configured and nowhere to export to. The example turns telemetry on for a
    # stack that has hyperdx; this one does not.
    out["DISCOVERY_OTEL_EXPORTER"] = "none"
    out["ADAPTER_OTEL_ENABLED"] = "false"

    # Built locally, because federation runs ahead of any published image.
    out["ADAPTER_IMAGE"] = "oan/network-adapter:local"

    # One label, used where a value has to be short and unique per network.
    short = network.split(".")[0]
    for key in UPSTREAM_IDS:
        if key in out:
            out[key] = f"{out[key]}.{short}"

    return out


def warn_if_bound(network, resolved):
    """Say which of this network's ports something else already holds.

    A warning, not an error: a port can be released between writing this file
    and starting the stack, so refusing here would be refusing on stale
    information. But finding out now beats finding out from a compose failure
    that names the port and not the service.
    """
    busy = []
    for name, port in sorted(ports_for(network).items()):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", int(port)))
            except OSError:
                busy.append(f"  {port}  {name}")
    if busy:
        print(f"network-env: these ports are already in use on this machine:")
        print("\n".join(busy))
        print("network-env: free them, or `network destroy` whatever holds them.")


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: network-env.py <networkId>")
    network = argv[1].strip()
    if not network:
        sys.exit("network-env: which network?")

    order, values = read_example()
    resolved = resolve(values, network)
    warn_if_bound(network, resolved)

    directory = STATE / network
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ".env"
    # Keys the example does not have go last, in a stable order, so a
    # regenerated file diffs cleanly against the one it replaces.
    extra = [k for k in sorted(resolved) if k not in order]
    path.write_text("\n".join(f"{k}={resolved[k]}" for k in list(order) + extra if k in resolved) + "\n")
    path.chmod(0o600)
    print(f"network-env: wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
