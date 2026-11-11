# OAN quick-start

One OAN network on one machine: registry, discovery, and the network adapter.
Everything runs in Docker on `localhost`.

## Before you start

- `git`
- Docker with Compose v2.20 or later — `docker compose version` must work
- `python3` with `cryptography` — `pip install cryptography`
- `curl`

## Run it

```sh
git clone -b development https://github.com/OpenAgriNet/helmcharts.git
cd helmcharts/quick-start
cp .env.example .env
```

Nothing in `.env` needs changing for a local run. Then:

```sh
make up-core                                 # registry and discovery
make keys                                    # this network's keypair
make register-network                        # its registry entry
make register-provider PROVIDER=mausamgram   # repeat per provider
make configure-adapter                       # render the config, start the adapter
```

The adapter serves the providers you register, and asks for a credential only
when you register the provider that needs it:

| provider | capability | needs |
|---|---|---|
| `pocra` | Agriculture Facility | nothing |
| `mausamgram` | Weather Observation | `MAUSAMGRAM_AUTH` |
| `agmarknet` | Mandi Price | base URL, token URL, access name, password |
| `vistaar` | Knowledge Advisory | base URL, token URL, client id, secret |
| `pmkisan` | Grievance and Scheme Application | two hosts, an AES key and nonce, two service tokens |

`pocra` needs nothing, so it is the one to start with. `pmkisan` is the odd one:
two capabilities on two hosts, registered together by the one command.

Every `*_MAPPING_URL` must point at a tag that exists in the published
helmcharts repo. One that 404s breaks every select for that capability, and
nothing in the error names the mapping.

`make configure-adapter` asks which deployment mode and whether to crawl other
networks, and remembers both in `.env`.

## Check it works

```sh
make ps
curl -s localhost:9201/health
```

Then the Postman collection — 38 requests:

```sh
newman run ../api-collection/OpenAgriNet.api-collection.json \
  -e ../api-collection/environments/OpenAgriNet.api.local-environment.json
```

The environment targets single mode. In multi mode, point the two adapter
variables at the separate consumer and provider adapters:

```sh
newman run ../api-collection/OpenAgriNet.api-collection.json \
  -e ../api-collection/environments/OpenAgriNet.api.local-environment.json \
  --env-var network-adapter-host=http://127.0.0.1:9202/consumer \
  --env-var network-adapter-publish=http://127.0.0.1:9200/publish
```

Green means the registry is seeded, signatures verify, the mappings work and
discovery is indexing.

POCRA and Knowledge can go red for reasons that are not your setup: POCRA
rate-limits with an empty 200, and Knowledge needs a credential only the
adapter reads.

## Talking to another network

```sh
make host-descriptor                        # publish who we are
make verify PEER=<their-host>               # see what they serve
make admit PEER=<their-host> DIRECTION=source PACKS=all
```

`source` lets us read from them, `requester` lets them ask us. Each direction
is its own decision, so a pair that reads from each other needs four admits
across the two networks.

`make host-descriptor` also starts the front door another network reaches you
at: a server block answering on your networkId, serving the three documents and
mapping `/discover` to the network module. It needs no proxy host and no
certificate in the admin UI.

**To run two networks on one machine, see [FEDERATION.md](FEDERATION.md).** It
covers the second directory, the ports, the local certificate authority, and
the two traps worth knowing up front: `NETWORK_BASE_URL` must carry no port,
and a published catalogue needs `visibleTo` naming the other network or the
crawl collects nothing.

## Calling the DSS through the adapters

An experience app of your own calls the DSS through two more adapters, so the
DSS is only reachable by a caller the registry knows. Neither experience-api nor
the DSS runs in this stack.

```
experience-api -> experience-adapter (signs) -> dss-adapter (checks) -> DSS
DSS -> dss-adapter (signs as the DSS) -> network-adapter /network and /provider
```

Set these in `.env`:

| Variable | What it is |
|---|---|
| `EXPERIENCE_ADAPTER_IMAGE` | an adapter image built from the network-adapter branch that adds `nonbecknrouter` and `streamResponses`; the released image has neither |
| `EXPERIENCE_SUBSCRIBER_ID` | the id experience-api sends as `EXPERIENCE_API_SENDER_ID` |
| `DSS_SUBSCRIBER_ID` | the id the DSS sends as `network_sender_id` |
| `DSS_URL` | where the DSS listens, as the adapter container sees it, e.g. `http://host.docker.internal:8077` |
| `DSS_ADAPTER_URL` | where the experience adapter finds the DSS adapter, with no trailing slash. Leave it unset when both run in this stack: it defaults to `http://dss-adapter:9204` |

Each adapter is registered, configured and started on its own. The experience
adapter needs no `DSS_URL`, and the DSS adapter needs no `EXPERIENCE_SUBSCRIBER_ID`.

```sh
make register-experience      # the experience adapter's entry, with its own keys
make configure-experience     # render its config and start it
make register-dss             # the DSS adapter's entry, with its own keys
make configure-dss            # render its config and start it
```

Then point the services at the adapters:

| Service | Setting | Value |
|---|---|---|
| experience-api | `EXPERIENCE_API_DSS_BASE_URL` | `http://localhost:9203/dss` |
| experience-api | `EXPERIENCE_API_SENDER_ID` | the same as `EXPERIENCE_SUBSCRIBER_ID` |
| DSS | `DSS_DISCOVERY_BASE_URL`, `DSS_INVOCATION_BASE_URL` | `http://localhost:9204/consumer` |
| DSS | `DSS_NETWORK_SENDER_ID` | the same as `DSS_SUBSCRIBER_ID` |
| DSS | `DSS_NETWORK_HOST_ID` | `BECKN_NETWORK_ID` |

Leave `DSS_NETWORK_SENDER_FROM_TURN_ENABLED` off. Turned on, the DSS would copy
the experience id into its calls to the network, and the signature would name
the DSS.

## When it breaks

| What you see | What to do |
|---|---|
| `adapter.yaml: is a directory` | `docker compose down`, `rmdir config/adapters/*/*.yaml`, `make configure-adapter` |
| `BUG: There can only ever be one graph vertex` | `make destroy`, then start again |
| a capability answers 404 | register its provider |
| `AUT_SUBSCRIBER_NOT_FOUND` | `make register-network` — it reads the key id back |
| the registry never comes up | give it two minutes on a cold volume, then `make logs SVC=sunbird-registry-service` |
| a select fails though discover worked | that capability's `*_MAPPING_URL` 404s — check the tag exists |

## Commands

```
make up        everything, including the public edge and observability
make up-core   registry and discovery only
make ps        what is running
make logs      follow everything, or SVC=<service> for one
make down      stop, keep the data
make destroy   stop and DELETE every volume
make help      the rest
```
