# Network of networks — demo runbook

Two whole OAN networks on one laptop, able to find and call each other.

Every command and every response below was run end to end on 2026-10-07, from
an empty machine. Timings are what it actually took.

Ports: `bharatvistar` gets 9300-9306, `mahavistara` 9400-9406.

**Postman:** import `demo/oan-network-of-networks.postman_collection.json`. Two
folders, Bharat and Maha, with every request below already filled in.

**B does not crawl.** `FEDERATION_CRAWL_PEERS=off` in its `.env`. With both
networks crawling, each mirrors the other, and B ends up re-importing its own
catalogue through A's copy of it -- and could overwrite its own with a stale
one. B still ANSWERS A's crawl; that is the network module, nothing to do with
this switch. And A's mandi is published `visibleTo` A alone, so there is
nothing of A's for B to take even if it did crawl. The script
allocates the next free block per network, so a third network just works.

---

## Before you start

Docker, Python 3, `openssl`, `curl`. The adapter image must exist locally:

```bash
cd ~/Workspace/OAN/network-adapter
docker build --network=host -f Dockerfile.adapter-with-plugins -t oan/network-adapter:local .
```

`--network=host` is not optional on this machine — IPv6 is broken here and
`go mod download` hangs without it. Takes about 4 minutes cold.

Everything else runs from `helmcharts/networkofnetwork`.

---

## 1. Stand up two networks  (~75s each)

```bash
cd ~/Workspace/OAN/helmcharts/networkofnetwork

make network NETWORK_ID=bharatvistar.oan.local
make network NETWORK_ID=mahavistara.oan.local
```

Each one brings up its own registry, Keycloak, discovery service, Postgres, two
mock providers, an adapter and a front door — then seeds the registry and
renders the adapter config. Re-running it is also how you apply a config
change.

Check what is deployed:

```bash
make ls
```

```
bharatvistar.oan.local                   9300-9306
mahavistara.oan.local                    9400-9406
```

---

## 2. Each network hosts its own identity  (instant)

```bash
make host-descriptor NETWORK_ID=bharatvistar.oan.local
make host-descriptor NETWORK_ID=mahavistara.oan.local
```

```
bharatvistar.oan.local now hosts its descriptor at revision 1
  descriptor  networks/bharatvistar.oan.local/.well-known/openagrinet
  keys        networks/bharatvistar.oan.local/.well-known/jwks.json
  governance  <kid>
  operational <kid>
```

Two signed documents per network, served over TLS at the network's own domain:
`/.well-known/openagrinet` (the Network Descriptor) and `/.well-known/jwks.json`.

- **Signature:** Ed25519 (`"algorithm": "Ed25519"`, RFC 9864 name) over the
  RFC 8785 (JCS) form of the descriptor minus its `signature`. Floats are
  refused, so every implementation rebuilds the same bytes.
- **Two kinds of key**, as the contract requires. The **governance** key
  (`keys/governance.key`, generated on first run, mode 600) signs the descriptor
  and never traffic. The **operational** keys are the registry entry's active
  keys -- the ones the adapters sign traffic with. The descriptor delegates them
  by kid in `operationalKeys`, under the governance signature.
- **kid** is each key's RFC 7638 thumbprint, so a rotated key gets a new kid on
  its own. To rotate an operational key, add it to the registry entry, re-run
  `host-descriptor`, and revoke the old one once peers have refreshed.
- **revision** must increase: re-running bumps it, and `oanfed publish` refuses
  one at or below what is already published. The JWKS is written before the
  descriptor, each in one atomic step.

Peers store only the delegated operational keys on `admit`, so the governance
key can never pass their traffic check.

---

## 3. Each network admits the other  (instant)

```bash
make admit NETWORK_ID=bharatvistar.oan.local PEER=mahavistara.oan.local
make admit NETWORK_ID=mahavistara.oan.local PEER=bharatvistar.oan.local
```

```
admitted mahavistara.oan.local at revision 1, status active
  discovery     https://mahavistara.oan.local/network/discover
  projectionTtl 3600s
  keys          1 published
  relationship  both
```

This fetches the other network's descriptor over TLS, verifies its signature
against the key it publishes, and records the decision to deal with it. Nothing
is trusted because it is on the same machine.

---

## 4. Give each network different data

This is the story: **A has only mandi prices, B has only weather.** Neither is
missing a capability — both adapters serve all four — the difference is what
each one has PUBLISHED.

```bash
# A publishes mandi
curl -s -X POST localhost:9300/publish/publish \
  -H 'Content-Type: application/json' -d @demo/publish-catalog-mandi.json | jq .

# B publishes weather
curl -s -X POST localhost:9400/publish/publish \
  -H 'Content-Type: application/json' -d @demo/publish-catalog.json | jq .
```

Both answer `ACCEPTED`. Each catalog is published `visibleTo` both networks, and
carries its own `bppId`/`bppUri` — catalog fields, which is how a consumer later knows where to
send a select.

---

## 5. A crawls B

```bash
curl -s -X POST localhost:9300/crawl/peers
```

No arguments. The crawler reads the registry: it lists the ACTIVE participants
with role `network`, drops its own entry, and takes each peer's discovery URL,
`projectionTtl` and `schemaPacks` from the record `make admit` wrote. So "who do
I crawl" is the set of networks you have admitted, and nothing else.

It also re-crawls on its own every `federationRefreshSeconds` (900s here), so in
a long demo this happens again without being asked.

```
catalogcrawler: crawled a network networkId=mahavistara.oan.local catalogs=1
```

---

## 6. The farmer asks ONE network and gets both

```bash
curl -s -X POST localhost:9301/discover \
  -H 'Content-Type: application/json' -d @demo/discover-farmer.json | jq .
```

```
catalog:mandi-price:agmarknet.bharatvistar          -> select at bharatvistar.oan.local
catalog:weather-observation:mausamgram.mahavistara  -> select at mahavistara.oan.local
```

One query, against network A only. A's own mandi catalogue, and B's weather
catalogue which A crawled. The farmer never named a network and never knew there
were two.

The payload carries no `context.networkId`. An omitted audience means no
audience filter at all, so the farmer sees everything A holds — its own and its
mirrored copies alike, without having to know that either exists.

**Use the no-networkId form for the demo.** Naming an audience filters to
catalogs published `visibleTo` that audience, and a crawled copy is stamped
with the audience the crawl asked with — which is not reliably ours:

```
ask A with no networkId           -> mandi-price, weather-observation
ask A as mahavistara.oan.local    -> mandi-price, weather-observation
ask A as bharatvistar.oan.local   -> mandi-price          (the mirror is missing)
```

The crawl asks the peer twice — once as the peer, once as us — and ours goes
last so our copy should win. Both calls are made (the peer's nginx log shows
two POSTs), but the second comes back empty while the same request sent by hand
returns the catalog. Unresolved; see section 3 of the todo. It does not affect
the farmer path, which names no audience.

---

## 7. Select each one — one local, one across the network

The consumer picks a catalog and copies its `bppId` into the select's
`context.receiverId`, and its `bppUri` into `context.bppUri`. Nothing else
decides where the request goes.

(The context names the two ends by DIRECTION — `senderId` and `receiverId` —
not by Beckn role. `bapId`/`bppId` are the older spelling and appear in no
payload here. `bppUri` stays, because it addresses the PROVIDER a catalog is
served from rather than saying who sent the envelope.)

**Mandi — `receiverId` is A, so A answers it itself:**

```bash
curl -s -X POST localhost:9300/provider/select \
  -H 'Content-Type: application/json' -d @demo/select-mandi-local.json | jq .
```

Answers `on_select` from `agmarknet.bharatvistar`, with prices from A's own mock.

**Weather — `receiverId` is B, so A sends it to B:**

```bash
curl -s -X POST localhost:9300/provider/select \
  -H 'Content-Type: application/json' -d @demo/select-cross-network.json | jq .
```

Answers `on_select` from `mausamgram.mahavistara` — the OTHER network's provider,
with three days of forecast.

Both requests arrive at the SAME endpoint on the SAME network. The only
difference is the `receiverId` the farmer's consumer copied out of the catalog.
In
A's log:

```
mahavistara.oan.local is not bharatvistar.oan.local; sending it to http://...
Signature generated: ...
Routing to ctx.Route to &model.Route{TargetType:"url", ...}
```

A recognised the request was not its own to answer, signed it as **itself**, and
sent it to the address the catalog carried. Nothing in A is configured with who
its peers are.

---

## Tear down

```bash
make down    NETWORK_ID=bharatvistar.oan.local   # stop, keep the data
make destroy NETWORK_ID=bharatvistar.oan.local   # delete the data, free the ports

make teardown                                    # destroy EVERY network, asks first
```

---

## Known rough edges — say these before someone finds them

| | |
|---|---|
| `federationIntent` decides what breadth of a peer's catalogue is mirrored, and is set to weather here. Discovery refuses an intent that narrows nothing, so there is no "mirror everything". | |
| Only `mausamgram` and `agmarknet` have mock containers. `vistaar` and `pocra` are in the certificate but have nothing behind them. | |
| `validateSign` is in no step chain, so a cross-network select is answered whether or not it is signed. | |
| Nothing re-reads a peer's descriptor on a schedule — `admit` is also the refresh, and a person has to run it. | |
| Nothing removes a mirrored catalog. Refresh happens; withdrawal does not, because discovery has no delete endpoint. | |
| A select routes on `receiverId` alone. Any id a caller invents is enough, and the address comes from the same request. | |

---

## If something breaks

| symptom | cause |
|---|---|
| adapter restart-loops on `applying 0001_init.sql` | a `destroy` took the crawler database with it. `CREATE DATABASE catalogcrawler` by hand, or destroy and bring it back up. |
| a rebuilt image seems to have no effect | `docker restart` reuses the container's existing layer. Re-run `make network NETWORK_ID=...`, which recreates. |
| `failed to get signing key: keyset not found` on a cross-network select | `sign` is running before the capability steps. It must come after them. |
| `endpoint '' is not supported` | a module mounted at an exact path with no trailing slash — routing reads the action from what is left of the URL. |
| a crawl returns `catalogs=0` | the intent, or the schema packs. Both are silent: discovery answers 200 with nothing. |
| a peer's front door answers as yourself | two stacks on one bridge share a DNS alias. Address containers by their project-prefixed names. |
