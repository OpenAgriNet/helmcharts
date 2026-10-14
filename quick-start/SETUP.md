# Quick-start setup

Install and run the OAN stack with Docker Compose. What the stack is and the
two adapter modes are in [README.md](README.md); this file is the steps.

Nothing is built here — the images are pulled. Locally that is about ten
minutes, most of it waiting for Keycloak.

|                      | Local                    | VM                          |
|----------------------|--------------------------|-----------------------------|
| Reached over         | `localhost`              | a public hostname, TLS      |
| Public edge (Nginx)  | not started              | started, on 80 and 443      |
| Observability        | not started              | optional, wants 2–4 GB      |
| Credentials          | shipped defaults are ok  | **must all be changed**     |
| Start, single mode (default) | `make up-core`   | `make up`                   |
| Start, multi mode    | `make up-core-multi`     | `make up-multi`             |

**Local** is Part 1. **VM** is Part 1, then Part 2 for the differences.

---

# Part 1 — Run it locally

## Step 1 — Prerequisites

- `git`
- Docker with **Compose v2.20 or later** — `docker compose version` must work, not `docker-compose`; the compose file uses `depends_on: required: false`, which older v2 releases reject
- `python3` with `cryptography` — `pip install cryptography`
- `curl`

## Step 2 — Get the repo

```sh
git clone -b development https://github.com/OpenAgriNet/helmcharts.git
cd helmcharts/quick-start
```

**Every command below runs from `quick-start/`** — it is where the compose file
and the Makefile live.

## Step 3 — Configure

```sh
cp .env.example .env
```

Locally, nothing in it needs changing. It publishes these on `localhost`:

```
8081  registry          9100  mock-imd
8080  keycloak          9101  mock-agmarknet
9990  keycloak admin
8090  discovery

single mode (default)   9200  network adapter (all three tiers)
multi mode              9200  provider adapter
                        9201  network adapter
                        9202  consumer adapter
```

If a port is already taken, change it in `.env` — that is the only edit a
local run needs. The single adapter's host port is
`NETWORK_ADAPTER_LAYER_PORT`; in multi mode the three are
`PROVIDER_ADAPTER_PORT`, `NETWORK_ADAPTER_PORT` and `CONSUMER_ADAPTER_PORT`.
Adapters reach each other inside the stack (by Compose service name in multi
mode, on `localhost:9200` inside the one container in single mode), so a port
change only changes what you type into Postman.

## Step 4 — Start it

Single mode (the default):

```sh
make up-core
```

Or multi mode, three adapters:

```sh
make up-core-multi
```

Either one runs the steps in the only order that works: registry and
discovery, then `bin/setup.py`, then the adapters. Allow up to five minutes
the first time — Keycloak on a cold volume.

`setup.py` generates a keypair per tier, registers seven participants and four
capability bindings, and renders all four adapter configs (the three tier
configs and the single adapter's). Nothing needs creating by hand. →
README Appendix C for why the order matters, Appendix D for what it wrote.

Switching mode later is running the other target; see
[README — How to switch](README.md#how-to-switch).

```sh
make ps
```

## Step 5 — The three tiers and their `.env` keys

Both modes run the same three tiers with the same keys; single mode runs them
as modules of one container. `setup.py` renders every adapter config from
these keys. **Do not edit the rendered `config/adapters/*.yaml`** — they are
regenerated and hold private keys. Change `.env`, re-run `make up-core` (or
`make up-core-multi`).

**Provider** — answers `select`, the only tier that calls an upstream. Serves
all four capabilities.

- `PROVIDER_SUBSCRIBER_ID`, and the four pairs that become binding keys:
  `MAUSAMGRAM_PARTICIPANT_ID` + `MAUSAMGRAM_CAPABILITY`,
  `AGMARKNET_PARTICIPANT_ID` + `AGMARKNET_CAPABILITY`,
  `VISTAAR_PARTICIPANT_ID` + `VISTAAR_CAPABILITY`,
  `POCRA_PARTICIPANT_ID` + `POCRA_CAPABILITY`.
- Upstream credentials: `AGMARKNET_ACCESS_NAME` + `AGMARKNET_PASSWORD`, and
  `VISTAAR_CLIENT_ID` + `VISTAAR_CLIENT_SECRET`. POCRA needs none.

**All four capabilities call real upstreams.** Mausamgram, mandi, knowledge
and POCRA are external hosts, so each needs its own base URL and path in
`.env` — and the stack needs egress to all four. Mandi and knowledge each also need a token
endpoint, since both issue short-lived tokens rather than taking a static
credential.

**Network** — fronts discovery: verifies the caller, passes `discover` and
`publish` on, and re-signs as itself.

- `NETWORK_SUBSCRIBER_ID`. `BECKN_NETWORK_ID` belongs to the discovery service
  behind it — a `discover` naming a different network finds nothing.

**Consumer** — the caller's entry. Sends `discover` to the network tier and
`select` straight to the provider tier.

- `CONSUMER_SUBSCRIBER_ID`.

Check the adapters came up:

```sh
# single mode: one container
docker logs network-adapter 2>&1 | grep -E 'Processor steps initialized|Server listening'

# multi mode: one per tier
docker logs provider-adapter 2>&1 | grep 'Processor steps initialized'
docker logs network-adapter  2>&1 | grep 'Server listening'
docker logs consumer-adapter 2>&1 | grep 'Server listening'
```

All four capability steps should be listed by name.

The container is called `network-adapter` in both modes, so `docker logs
network-adapter` works in either. Compose commands take the service name,
which differs: `make logs SVC=network-adapter-layer` in single mode,
`make logs SVC=network-adapter` in multi mode.

## Step 6 — Verify end to end

Run the API collection in `../api-collection/`. One collection serves both
modes, since the paths are the same; the environment picks the mode:

| Mode | Collection | Environment |
|---|---|---|
| single (default) | `OpenAgriNet.api-collection.json` | `OpenAgriNet.network-adapter.local-environment.json` (every adapter host is `:9200`) |
| multi | `OpenAgriNet.api-collection.json` | `OpenAgriNet.local-environment.json` |

Import the collection and the environment for your mode into Postman and
select the environment, or every `{{...-host}}` resolves to nothing. Or run it
with newman:

```sh
# single mode
newman run ../api-collection/OpenAgriNet.api-collection.json \
  -e ../api-collection/OpenAgriNet.network-adapter.local-environment.json

# multi mode
newman run ../api-collection/OpenAgriNet.api-collection.json \
  -e ../api-collection/OpenAgriNet.local-environment.json
```

One folder per capability plus a health folder. Each capability folder is
publish, then discover, then select, in that order — so run publish first.
Every request's `transactionId`, `messageId` and `timestamp` are filled fresh
by the collection's pre-request script; the rest of each body comes from
`capability-examples/`, unedited.

Green means the registry is seeded, signatures verify both ways, all four
mappings work and discovery is indexing.

POCRA is the one folder whose empty answer is LOGGED rather than failed: it
returns 200 with no providers both when nothing is nearby and when it is
rate-limiting, and the two are indistinguishable. Knowledge is also the one
folder that can fail on a missing credential rather than on wiring — it
authenticates with OAuth2 client credentials that only the adapter reads, at
call time.

To point it at another deployment, edit the **environment** file, not the
collection. → README Appendix N.

---

# Part 2 — Run it on a VM

Four differences. **V1 comes before Part 1 Step 2**, because it installs `git`.

## Step V1 — Prepare the VM

Nothing is cloned yet, so fetch the script rather than running it from the repo:

```sh
curl -fsSL https://raw.githubusercontent.com/OpenAgriNet/helmcharts/development/quick-start/bin/bootstrap-ubuntu.sh | bash
```

Installs `git`, `make`, `python3-cryptography` and Docker from Docker's own apt
repo, then adds you to the `docker` group — **log out and back in** for that to
take effect. Idempotent, and it deliberately does not clone, write `.env` or
start anything.

**8 GB** runs the stack; **16 GB** for the observability tier.

Then Part 1 Steps 2 and 3 as written.

## Step V2 — Change every credential

`.env.example` ships working defaults, which means they are public:

```
POSTGRES_PASSWORD    KEYCLOAK_ADMIN_PASSWORD    KEYCLOAK_ADMIN_CLIENT_SECRET
KEYCLOAK_DEFAULT_USER_PASSWORD
```

Change all four before the VM is reachable by anyone but you. Adapter keypairs
are the exception — `setup.py` generates those and never writes them to `.env`.

## Step V3 — Start it

Single mode (the default):

```sh
make up
```

Or multi mode: `make up-multi`.

`make up`, not `make up-core`: two more tiers on top of Part 1's —
nginx-proxy-manager on 80 and 443, and HyperDX. **This is the step that makes
the VM reachable from the internet.** The mode decides what the proxy hosts in
README Appendix B forward to.

Switching mode on a running VM (`make up` ⇄ `make up-multi`) recreates the
adapters and restarts nginx-proxy-manager so it picks up the new addresses:
the public hosts drop connections for a few seconds. Repoint the proxy hosts
(Appendix B) in the same window.

## Step V4 — Decide what is exposed

Everything except the edge's 80 and 443 is bound to `127.0.0.1`, written
literally in `docker-compose.yml` rather than taken from a variable. So the
registry, Keycloak and the databases are not publicly reachable — deliberately.
Signing is off in every quick-start adapter config, so the adapter ports must
stay on `127.0.0.1` too.

Proxy hosts, certificates, and the `/publish` deny every host gets →
**README Appendix B**. Read it before pointing DNS at the box.

## Step V5 — Reach the ports bound to 127.0.0.1

Single mode needs only the adapter's 9200, plus the registry and Keycloak:

```sh
ssh -L 9200:127.0.0.1:9200 -L 8081:127.0.0.1:8081 -L 8080:127.0.0.1:8080 -N you@the-vm
```

Multi mode adds the consumer's 9202 (`-L 9202:127.0.0.1:9202`). The
collections' defaults then work unchanged, since they already point at
`127.0.0.1`.

## Step V6 — Observability (optional)

```sh
make observability
```

HyperDX on `127.0.0.1:8085`, OTLP on 4317/4318 — and, once you add its proxy
host, on a public hostname behind an NPM Access List, which is how you read it
without a tunnel. Route + credential → **README Appendix B**. → Appendix H,
which is honest about how much actually arrives.

Every container's stdout lands there too, next to the traces — so this is also
what replaces keeping `docker compose logs -f` open. How that is wired, and the
one gotcha, are in Appendix H.

`docker compose restart nginx-proxy-manager` after this command, the first
time: NPM only resolves `hyperdx` on reload.

---

## If something is wrong

- **`unrecognized step: <name>`** — `ADAPTER_IMAGE` predates the config.
  README Appendix G.
- **404 `NET_ENTITY_NOT_FOUND`** — no provider step claimed the payload; a
  binding key disagrees with `.env`. Appendix F.
- **404 naming a binding with no active record** — the step matched but the
  registry has no `ProviderSchema` row for it. Appendix F.
- **502 from a `select`** — the upstream answered non-2xx. Appendix F.
- **NPM's default page, or a 502 that worked yesterday** — Appendix B.
- **An adapter config is a directory** — something started before
  `setup.py`. Appendix F.

Full set, with what to run for each →
[README Appendix F](README.md#appendix-f--when-it-does-not-work).
