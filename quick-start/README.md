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

Providers are `mausamgram`, `agmarknet`, `vistaar` and `pocra`. The adapter
serves the ones you register.

`make configure-adapter` asks which deployment mode and whether to crawl other
networks, and remembers both in `.env`.

## Check it works

```sh
make ps
curl -s localhost:9201/health
```

Then the Postman collection — 18 requests:

```sh
newman run ../api-collection/OpenAgriNet.api-collection.json \
  -e ../api-collection/OpenAgriNet.network-adapter.local-environment.json
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

For another network to reach you, paste
`config/reverse-proxy/npm-advanced/network.conf` into the network proxy host's
Advanced tab. Without it `/discover` is a 404 and they collect nothing.

To run two networks on one machine, set `STACK_NAME` and `STACK_PREFIX` in the
second one's `.env`, along with its own ports, `BECKN_NETWORK_ID` and
`NETWORK_BASE_URL`. The last two are the network's identity and the address it
publishes, so two stacks sharing either are one network as far as anyone else
can tell.

## When it breaks

| What you see | What to do |
|---|---|
| `adapter.yaml: is a directory` | `docker compose down`, `rmdir config/adapters/*/*.yaml`, `make configure-adapter` |
| `BUG: There can only ever be one graph vertex` | `make destroy`, then start again |
| a capability answers 404 | register its provider |
| `AUT_SUBSCRIBER_NOT_FOUND` | `make register-network` — it reads the key id back |
| the registry never comes up | give it two minutes on a cold volume, then `make logs SVC=sunbird-registry-service` |

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
