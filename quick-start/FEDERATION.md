# Two networks on one machine

A network of networks, with nothing stubbed: two whole OAN deployments, each
with its own registry, discovery service and adapter, finding and calling each
other over real TLS.

Throughout, the two are called **net-a** and **net-b**. Pick your own names —
they only have to be distinct and end in a domain you can put in `/etc/hosts`.

| | net-a | net-b |
|---|---|---|
| directory | this one | a copy of it |
| networkId | `net-a.oan.dev` | `net-b.oan.dev` |
| front door | 443 | 8443 |
| adapter | 9201 | 9202 |
| serves | one capability | another |

net-a crawls net-b, so net-a ends up answering for both. net-b answers net-a's
crawl but does not crawl back.

Read [README.md](README.md) first: this assumes you can already bring up one
network and register a provider.

---

## What is automatic, and what is not

Automatic, on `make up`:

- a local certificate authority, shared by every network on this machine
- a TLS certificate for this network's own name, re-issued if the name changes
- the `oan-federation` Docker bridge the two networks meet on
- the front door — an nginx server block that needs **no** proxy host and
  **no** certificate upload in the admin UI

Yours to do:

- a second directory, with its own `.env`
- `/etc/hosts` entries, so your own browser and curl can resolve the names
- importing the CA, only if you want to open the documents yourself

## Why TLS at all

Because three things refuse a plain http address, and working around any of
them would hide what a local run is meant to prove:

- the registry pins a participant's `baseUrl` to `^https://`
- `bin/oanfed.py` refuses to admit a network whose `services.discovery` is not
  an https URL
- the crawler calls that URL with Go's default trust store, and the adapter has
  no setting anywhere to skip verification

So: one CA, kept outside the repo at `~/.oan/federation-ca`, and a certificate
per network signed by it. Another network verifies against the CA, which is
real verification. What a laptop still cannot prove is that it **owns** the
name — which is why the CA is trusted by path rather than installed into any
system store.

---

## 1. The second directory

Copy this one, leaving behind everything that belongs to the first network:

```sh
rsync -a --exclude '.env' --exclude 'keys/' --exclude '.well-known/' \
         --exclude 'tls/' --exclude 'bin/__pycache__/' \
         --exclude 'config/reverse-proxy/federation/server.conf' \
         --exclude 'config/adapters/*/*-adapter.yaml' \
         --exclude 'config/adapters/single-adapter/network-adapter.yaml' \
         ./ ../net-b/
cp .env.example ../net-b/.env
```

Keys and certificates are per network and must not be shared. Two stacks with
the same key are one network as far as anyone else can tell.

## 2. The two `.env` files

| key | net-a | net-b |
|---|---|---|
| `BECKN_NETWORK_ID` | `net-a.oan.dev` | `net-b.oan.dev` |
| `NETWORK_BASE_URL` | `https://net-a.oan.dev` | `https://net-b.oan.dev` |
| `FEDERATION_CRAWL_PEERS` | `on` | `on` |
| `FEDERATION_REFRESH_SECONDS` | `60` | `60` |

**`NETWORK_BASE_URL` carries no port, on either side.** Inside the shared
bridge both front doors listen on 443; the `:8443` below is only how your own
machine reaches the second one. A port here is published to the other network
as part of your address, and it will dial it.

`FEDERATION_REFRESH_SECONDS=60` is a demo value, so a crawl happens while you
watch. The shipped default is 900 and should stay 900 anywhere real.

The second network also needs its own ports, or it fights the first for them.
`.env.example` carries the whole block, commented, under *running a second
network on one machine* — uncomment it in the second `.env`.

Check every `*_MAPPING_URL` points at a tag that exists in the published
helmcharts repo. A mapping that 404s breaks every select, on both networks,
with nothing in the error naming the mapping.

## 3. Bring both up

In each directory:

```sh
make up-core
```

`up-core` skips ClickHouse and Grafana — two stacks of observability wants
around 8 GB you do not need here. The front door starts later, in step 5.

The first run of each prints:

```
created the oan-federation bridge -- every network on this machine shares it
issued a certificate for net-a.oan.dev, valid 825 days
```

Never use `sudo`. The front door's rendered config has to stay writable by you,
and a sudo run leaves it owned by root.

## 4. Fill each registry

Same sequence in each directory, in this order:

```sh
make keys
make register-network
make register-provider PROVIDER=<name>
make configure-adapter
```

`configure-adapter` renders the adapter config from the registry **and**
restarts the adapters. That is the restart; there is no separate one. Editing
a rendered config by hand achieves nothing — it is overwritten from the
registry on the next run.

`register-network` **creates only**. If you later change `BECKN_NETWORK_ID` or
`NETWORK_BASE_URL`, the stored record keeps the old value and nothing warns
you. `make destroy` and start again — a participant id is never reusable.

## 5. Publish the documents

```sh
make host-descriptor
```

Three documents, signed with this network's key, plus the front door that
serves them. It ends with:

```
served at revision 1 -- peers can fetch it now
```

checked against the real CA, exactly as another network checks it. If it warns
instead, run `make restart-edge` and try again.

This is also what starts nginx and records where this network answers, in
`~/.oan/networks.json`, so the commands below need no addresses.

### Looking at them yourself

Add to `/etc/hosts`:

```
127.0.0.1  net-a.oan.dev net-b.oan.dev
```

Then, after importing `~/.oan/federation-ca/ca.crt` as a trusted authority:

- `https://net-a.oan.dev/.well-known/openagrinet`
- `https://net-b.oan.dev:8443/.well-known/openagrinet`

`:8443` because only one stack can hold 443. Without it you reach the *first*
network's front door, which has no server block for the second one's name and
rejects the handshake — a browser reports that as a bare connection failure.

If your TLD is HSTS-preloaded in the browser (`.dev` is), there is no
"proceed anyway" button, so the CA import is required rather than optional.

## 6. Check each can read the other

```sh
# in net-a
make verify PEER=net-b.oan.dev
```

Fetches the descriptor, checks its signature against the published JWKS, reads
the schema-pack list. Changes nothing. Do it both ways before admitting — a
failure here fails everything after it.

## 7. Agree, both ways

```sh
# in net-a: we read from them
make admit PEER=net-b.oan.dev DIRECTION=source    PACKS=all

# in net-b: they may ask us
make admit PEER=net-a.oan.dev DIRECTION=requester PACKS=all
```

Each side records only its own half, which is why it runs twice. Ours says
nothing about theirs.

`FEDERATION_CRAWL_PEERS` only enables crawling at all. **The agreement decides
what is crawled** — without both halves, nothing happens and the log says
`crawlable=0 admitted=0`.

Omit `PACKS` to be shown what the direction could cover before committing:

```
name the packs this agreement covers, or `all` for every one of them.

  net-b.oan.dev publishes 1:
    https://.../WeatherObservation/v0.1/attributes.yaml
```

Then name them, comma-separated. `all` is a snapshot of what they publish
**today**, not a standing rule — re-run to take in a new pack, because an
agreement that silently widened would not be one. Re-running replaces that
direction's list rather than adding to it; the other direction is untouched.

`source` is checked against *their* published packs, `requester` against ours.

## 8. Publish a catalogue, and watch the crawl

Into net-b, on loopback:

```sh
curl -X POST http://127.0.0.1:9202/publish \
  -H 'Content-Type: application/json' -d @<catalogue>.json
```

Two fields decide whether this works across networks:

- **`publishDirectives[].visibleTo`** must name the other network. Omitted, it
  resolves to the publishing network alone, and the crawl collects nothing
  while reporting an honest empty page.
- **`bppUri` and `bppId`** on each catalogue. The adapter does not stamp them;
  they come from this payload. A select is sent to `bppUri` + `/<action>`, so
  `bppUri` is the other network's base URL — again with no port.

Then, on net-a:

```sh
make logs SVC=network-adapter
```

```
catalogcrawler: asking a network networkId=net-b.oan.dev url=https://net-b.oan.dev/discover
catalogcrawler: a network answered catalogs=1 resources=1
catalogcrawler: network crawled published=1 returned=1 servedUntil=...
```

`catalogs=0` with *"a network shared everything it has"* is not a failure: the
crawl worked and there was nothing visible to you. Check `visibleTo`.

## 9. Discover and select

Both against net-a's own consumer mount:

```sh
curl -X POST http://127.0.0.1:9201/consumer/discover -d @discover.json
curl -X POST http://127.0.0.1:9201/consumer/select   -d @select.json
```

The discover answers from net-a's own discovery, which now holds net-b's
catalogues as copies with a TTL. The caller cannot tell which network served
which until it reads `bppId` — that is the whole point.

For a select that crosses, take `receiverId` and `bppUri` from the
`on_discover` answer and leave `senderId` as your own network. The call leaves
net-a, arrives at net-b's front door as `/select`, and is mapped to net-b's
`/provider/select`.

---

## What is public

Only the reverse proxy's 80 and 443 leave the machine. The registry, discovery
service, adapter and mocks are all bound to loopback.

| on the networkId | goes to |
|---|---|
| `/.well-known/openagrinet`, `jwks.json`, `schema-packs.json` | files |
| `/discover` | the network module |
| `/select` `/init` `/confirm` `/status` `/track` `/cancel` `/update` `/rate` `/support` | the provider module |
| `/network`, `/network/…`, `/provider`, `/provider/…` | the same two modules, for a `bppUri` addressed that way |
| anything else | 404 |

Deliberately **not** reachable, and the first two for good reason:

- `/consumer/…` takes **unsigned** requests — anything that reaches it can
  originate a call as this deployment
- `/publish` has **no authentication at all**
- `/crawl` is an operator surface

Use loopback for those: `http://127.0.0.1:<adapter port>/consumer/<action>` and
`.../publish`.

## Certificates

`bin/network-certs.sh`, run for you by `make up`. One CA at
`~/.oan/federation-ca` (override with `OAN_CA_DIR`), and a certificate per
network carrying the network's name and its providers' names in a
subjectAltName.

`make certs` re-issues this network's certificate. You should not need it —
`make up` re-issues automatically when `BECKN_NETWORK_ID` changes, because a
certificate for the old name fails at the far end and says nothing about why.

For a real deployment see [CERTIFICATES.md](CERTIFICATES.md). This file is the
local case only.

## Teardown

```sh
make down       # stop, keep the data
make destroy    # stop and delete every volume
```

In both directories. The shared bridge and the CA survive on purpose, so the
next run reuses the same trust.

## When it breaks

| What you see | What it means |
|---|---|
| `crawlable=0 admitted=0` | no agreement yet — run `make admit` on both sides |
| `connection refused` to the other network | its `NETWORK_BASE_URL` carries a port, or its front door is not running |
| `catalogs=0`, "shared everything it has" | the crawl worked; `visibleTo` does not name you |
| `host-descriptor` warns instead of confirming | `make restart-edge`, then re-run it |
| `verify` fails on the signature | that side has not re-run `host-descriptor` since its registry entry changed |
| `server.conf is not yours to write` | a `sudo` run; the message names the `chown` |
| `tlsv1 unrecognized name` | wrong port — that front door has no block for this name |
| browser blocks with no "proceed" | the CA is not imported, and the TLD is HSTS-preloaded |
| a select fails but the crawl worked | the mapping URL for that capability 404s |
| `names no sender` / `names no receiver` | `context.senderId` / `context.receiverId` missing; the adapter will not guess them |
