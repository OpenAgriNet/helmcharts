#!/usr/bin/env python3
"""Give each network its own block of host ports, once, and remember it.

Two networks on one machine cannot both publish 9200. Each gets a contiguous
block, and the block it got is RECORDED -- so bringing a network back up lands
on the ports it had, and `make network-ls` can say what is where.

Recorded rather than derived from the name. Hashing a network id into a block
needs no file, but two names can hash to the same block and the only symptom is
a port already in use at start-up, on whichever network happened to come second.
A file costs one more thing to keep, and cannot collide.

The file is the allocation, not a cache: deleting it and bringing a network back
up moves it to a different block, which is why `network destroy` clears its own
entry rather than the whole file.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE.parent / "networks"
ALLOCATIONS = STATE / "ports.json"

# Where the blocks start, and how wide one is. 100 apart so a block's ports stay
# readable -- 9300, 9301, 9302 belongs to one network at a glance, which matters
# when two stacks are running and a log line names a port.
BASE = 9300
WIDTH = 100

# What a block holds, in order. The names are the .env variables they fill.
PORTS = [
    "ADAPTER_PORT",
    "DISCOVERY_PORT",
    "REGISTRY_PORT",
    "KEYCLOAK_PORT",
    "MOCKIMD_PORT",
    "MOCKAGMARKNET_PORT",
    "FRONT_DOOR_PORT",
]


def load():
    if not ALLOCATIONS.exists():
        return {}
    return json.loads(ALLOCATIONS.read_text())


def save(allocations):
    ALLOCATIONS.parent.mkdir(parents=True, exist_ok=True)
    ALLOCATIONS.write_text(json.dumps(allocations, indent=2, sort_keys=True) + "\n")


def block_for(network):
    """This network's block index, allocating the lowest free one if it has none."""
    allocations = load()
    if network in allocations:
        return allocations[network]

    taken = set(allocations.values())
    index = 0
    while index in taken:
        index += 1
    allocations[network] = index
    save(allocations)
    return index


def ports_for(network):
    """The network's ports, as the .env variables they fill."""
    start = BASE + block_for(network) * WIDTH
    return {name: start + offset for offset, name in enumerate(PORTS)}


def release(network):
    """Forget a network's block, so the next one may take it."""
    allocations = load()
    if allocations.pop(network, None) is not None:
        save(allocations)


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: network-ports.py <ports|release|list> [network]")

    command = argv[1]
    if command == "list":
        for network, index in sorted(load().items(), key=lambda kv: kv[1]):
            start = BASE + index * WIDTH
            print(f"{network:40} {start}-{start + len(PORTS) - 1}")
        return 0

    if len(argv) < 3:
        sys.exit(f"usage: network-ports.py {command} <network>")
    network = argv[2]

    if command == "ports":
        for name, port in ports_for(network).items():
            print(f"{name}={port}")
        return 0
    if command == "release":
        release(network)
        return 0
    sys.exit(f"unknown command: {command}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
