# OAN quick-start

The whole OAN stack in Docker Compose: a registry, a discovery service, three
adapters, and two mock upstreams standing in for real provider APIs.

Nothing is built here — the images are pulled. Locally that is about ten
minutes, most of it waiting for Keycloak.

## The stack in one picture

```
  consumer
     │
     ▼
  experience adapter                     the only one a consumer calls
     │
     ├── discover ──►  network adapter  ──►  discovery service
     │
     └── select   ──►  provider adapter ──►  mockimd
                                        └─►  mockagmarknet
```

`publish` runs the other way: the provider adapter sends it to the network
adapter, which files the catalogue in discovery.

All three adapters read the registry — who signed this request, and where this
capability's provider lives.

A `select` answers in the same HTTP round trip — there is no callback.

That is the request order. **Startup order is the reverse**: the experience
adapter depends on the other two, so Compose brings them up first. The steps
below follow startup order, so they work top to bottom.

## Which path are you on?

|                      | Local                    | VM                          |
|----------------------|--------------------------|-----------------------------|
| Reached over         | `localhost`              | a public hostname, TLS      |
| Public edge (Nginx)  | not started              | started, on 80 and 443      |
| Observability        | not started              | optional, wants 2–4 GB      |
| Credentials          | shipped defaults are ok  | **must all be changed**     |
| Command              | `make up-core`           | `make up`                   |

**Local** is Part 1. **VM** is Part 1, then Part 2 for the differences.

---

# Part 1 — Run it locally

## Step 1 — Prerequisites

- `git`
- Docker with **Compose v2** — `docker compose version` must work, not `docker-compose`
- `python3` with `cryptography` — `pip install cryptography`
- `curl`

## Step 2 — Get the repo

```sh
git clone -b feat/4-docker-compose https://github.com/OpenAgriNet/helmcharts.git
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
8081  registry        9200  provider adapter     9100  mockimd
8080  keycloak        9201  network adapter      9101  mockagmarknet
9990  keycloak admin  9202  experience adapter
8090  discovery
```

If one is already taken, change it here — that is the only edit a local run
needs. Adapters reach each other by Compose service name, so it only changes
what you type into Postman.

## Step 4 — Start it

```sh
make up-core
```

Three tiers, in the only order that works: registry and discovery, then
`bin/setup.py`, then the mocks and the three adapters. Allow up to five minutes
the first time — Keycloak on a cold volume.

`setup.py` generates a keypair per adapter, registers five participants and two
capability bindings, and renders the three adapter configs. Nothing needs
creating by hand. → Appendix C for why the order matters, Appendix D for what
it wrote.

```sh
make ps
```

## Step 5 — Provider layer

Answers `select`, and the only layer that calls an upstream. Serves both
capabilities from one adapter.

`.env` keys: `PROVIDER_SUBSCRIBER_ID`, and the two pairs that become binding
keys — `PROVIDER_PARTICIPANT_ID` + `PROVIDER_CAPABILITY`,
`MANDI_PARTICIPANT_ID` + `MANDI_CAPABILITY` — plus `MANDI_TOKEN`.

```sh
docker compose logs provider-adapter | grep 'Processor steps initialized'
```

Both capability steps should be listed by name.

## Step 6 — Network layer

Fronts discovery: verifies the caller, passes `discover` and `publish` on, and
re-signs as itself.

`.env` keys: `NETWORK_SUBSCRIBER_ID`. `APP_NETWORK_ID` belongs to the discovery
service behind it — a `discover` naming a different network finds nothing.

```sh
docker compose logs network-adapter | grep 'Server listening'
```

## Step 7 — Experience layer

The consumer's edge. Sends `discover` to the network layer and `select`
straight to the provider layer, per `config/adapters/routing-experience.yaml`.

`.env` keys: `EXP_SUBSCRIBER_ID`.

```sh
docker compose logs exp-adapter | grep 'Server listening'
```

`setup.py` renders all three configs from these keys. **Do not edit the
rendered `config/adapters/*.yaml`** — they are regenerated and hold private
keys. Change `.env`, re-run `make up`.

## Step 8 — Verify end to end

Import both files from `../postman-collection/` into Postman —
`api-collection.json` and `local_postman_environment.json`, which already
points at localhost. Or:

```sh
newman run ../postman-collection/api-collection.json
```

**6 requests, 32 assertions**, one folder per capability. Each folder
publishes, discovers, then selects, so run publish before discover the first
time. Green means the registry is seeded, signatures verify both ways, both
mappings work and discovery is indexing.

To point it at another deployment, edit the **environment** file, not the
collection. → Appendix N.

---

# Part 2 — Run it on a VM

Four differences. **V1 comes before Part 1 Step 2**, because it installs `git`.

## Step V1 — Prepare the VM

Nothing is cloned yet, so fetch the script rather than running it from the repo:

```sh
curl -fsSL https://raw.githubusercontent.com/OpenAgriNet/helmcharts/feat/4-docker-compose/quick-start/bin/bootstrap-ubuntu.sh | bash
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
POSTGRES_PASSWORD    KEYCLOAK_ADMIN_PASSWORD    KEYCLOAK_SECRET
REGISTRY_DEFAULT_USER_PASSWORD
```

Change all four before the VM is reachable by anyone but you. Adapter keypairs
are the exception — `setup.py` generates those and never writes them to `.env`.

## Step V3 — Start it

```sh
make up
```

`make up`, not `make up-core`: two more tiers on top of Part 1's three —
nginx-proxy-manager on 80 and 443, and HyperDX. **This is the step that makes
the VM reachable from the internet.**

## Step V4 — Decide what is exposed

Everything except the edge's 80 and 443 is bound to `127.0.0.1`, written
literally in `docker-compose.yml` rather than taken from a variable. So the
registry, Keycloak and the databases are not publicly reachable — deliberately.

Proxy hosts, certificates, and the `/publish` deny every host gets →
**Appendix B**. Read it before pointing DNS at the box.

## Step V5 — Reach the loopback ports

```sh
ssh -L 9202:127.0.0.1:9202 -L 9200:127.0.0.1:9200 \
    -L 8081:127.0.0.1:8081 -L 8080:127.0.0.1:8080 -N you@the-vm
```

The collection's defaults then work unchanged, since they already point at
loopback.

## Step V6 — Observability (optional)

```sh
make observability
```

HyperDX on `127.0.0.1:8085`, OTLP on 4317/4318. → Appendix H, which is honest
about how much actually arrives.

---

## If something is wrong

- **`unrecognized step: <name>`** — `ADAPTER_IMAGE` predates the config.
  Appendix G.
- **404 `NET_ENTITY_NOT_FOUND`** — no provider step claimed the payload; a
  binding key disagrees with `.env`. Appendix F.
- **404 naming a binding with no active record** — the step matched but the
  registry has no `ProviderSchema` row for it. Appendix F.
- **502 from a `select`** — the upstream answered non-2xx. Appendix F.
- **NPM's default page, or a 502 that worked yesterday** — Appendix B.
- **An adapter config is a directory** — something started before
  `setup.py`. Appendix F.

Full set, with what to run for each → **Appendix F**.

---

## Appendices

Reference, read on demand. Nothing here is a step.

| | |
|---|---|
| **A** | What is here, and what is not |
| **B** | Reaching it: the edge, routes, certificates, tunnels |
| **C** | Startup order, and why it is that order |
| **D** | What is in the registry, and why you did not create it |
| **E** | How a request flows |
| **F** | When it does not work |
| **G** | Updating a deployment that is already running |
| **H** | Telemetry |
| **I** | Schema validation |
| **J** | About the mapping files |
| **K** | The layout |
| **L** | Renaming this directory |
| **M** | Starting over |
| **N** | What the collection demonstrates |

## Appendix A — What is here, and what is not

Running: **registry** (SunbirdRC + Postgres + Keycloak), **discovery**
(catalogue search + Postgres), **three adapters** (same image, three configs),
**two mock upstreams** standing in for Mausamgram and Agmarknet. Behind
profiles: **nginx-proxy-manager** (`reverse-proxy`, the only container on a
routable interface) and **hyperdx** (`observability`, ClickStack — the
heaviest thing here).

Deliberately absent:

- **A route to the registry.** Reachable from inside the Compose network and
  over an SSH tunnel, nowhere else. Nothing in front of it authenticates, and
  SunbirdRC uses POST for reads *and* writes, so a route would expose creates
  as readily as searches. That is why `setup.py` seeds everything — there is no
  second way in.
- **A real provider API.** The mocks answer the same shapes. Pointing a
  capability at something real is a registry write plus a base URL in `.env`;
  the adapter reads the address per request rather than holding it.

## Appendix B — Reaching it

### The adapters, through Nginx Proxy Manager

NPM owns 80 and 443 and is the whole public surface. Its routing table is
**rows in a SQLite database** in the `npm-data` volume, not a config file — so
setup is a one-time click-through and that volume is the only copy. Back it up.

**First boot.** Tunnel to the admin UI and change the shipped login before
creating anything:

```sh
ssh -L 81:127.0.0.1:81 -N you@the-vm     # then http://127.0.0.1:81
```

It logs in with `admin@example.com` / `changeme`, live from first boot.

**One proxy host per adapter.** Hosts → Proxy Hosts → Add:

| Domain | Forward Hostname | Port | Then |
|---|---|---|---|
| `exp.oan.example.com` | `exp-adapter` | 9202 | paste `config/reverse-proxy/npm-advanced/exp.conf` into **Advanced** |
| `network.oan.example.com` | `network-adapter` | 9201 | — |
| `provider.oan.example.com` | `provider-adapter` | 9200 | — |

Scheme `http` for all three — TLS terminates at NPM and the hop inward is
inside `oan-edge`. Turn on **Block Common Exploits**; leave Websockets off.

Three hosts rather than one with path prefixes, so a rate limit or a block
attaches to a hostname instead of a regex in a textarea, and each gets its own
certificate. An unknown `Host` gets NPM's default page, not an adapter.

**Certificates.** SSL tab → Request a new certificate → Force SSL → HTTP
validation. Two things must be true, and both are easy to miss:

- a public DNS **A record** per hostname, pointing at the VM — use a static
  address unless you enjoy redoing this after every stop/start;
- **port 80 open to `0.0.0.0/0`**, not to your address. Let's Encrypt fetches
  the challenge from its own servers, whose addresses you cannot enumerate. A
  security group scoped to your IP fails with a challenge timeout that looks
  nothing like a firewall problem.

If 80 must stay closed, use **DNS validation** — NPM ships the Route 53 plugin,
so an access key with `route53:ChangeResourceRecordSets` needs no inbound
request. It is also the only option for a wildcard. Renewal reuses whichever
method you chose, so a temporarily-correct DNS record or SG rule fails silently
in sixty days.

### `POST /publish` returns 403 on all three hosts

Not optional hardening. The provider adapter's module at `/` verifies the
sender's signature; `oanProviderPublish`, on the exact path `/publish`, has
**no signature check at all**, because its intended caller is the provider's
own catalogue system inside the trust boundary. A proxy host pointed at
`provider-adapter:9200` therefore exposes `<host>/publish` to anyone.

NPM's UI cannot route a host while withholding one path, so the block lives in
`config/reverse-proxy/npm-custom/server_proxy.conf`, which NPM includes in
**every** proxy host's server block — a mounted file, not a click, so not
something to remember on one host out of three.

To let a real catalogue system publish, give it a tunnel or put it in the VPC
and let it reach `provider-adapter:9200` directly. Do not turn that `deny` into
an `allow`.

### Which nginx config loads itself, and which is a paste job

| File | How it applies |
|---|---|
| `npm-custom/http_top.conf` | **Automatic**, top of the `http` block. The `exp` rate-limit zone and `limit_req_status 429`. |
| `npm-custom/server_proxy.conf` | **Automatic**, every server block. The `/publish` deny. |
| `npm-advanced/exp.conf` | **Manual** — paste into the experience host's Advanced tab. `limit_req` for that host only; a 10 r/s ceiling on signed peer traffic would throttle for no gain. |

The manual one is in a file anyway because NPM's Advanced field is a textarea
in a database row — nothing diffs it and nothing reviews it.

### Adding a route for another service

Two steps, and the first is in git rather than the UI. NPM sits on `oan-edge`,
where only the three adapters resolve, so a proxy host pointed at `registry` or
`hyperdx` 502s rather than quietly working. **The UI alone cannot widen what is
public** — that is the property worth keeping.

1. **Put the service on `oan-edge`** in `docker-compose.yml`
   (`networks: [oan-internal, oan-edge]`), then
   `docker compose up -d some-service nginx-proxy-manager`. NPM needs the
   restart to resolve a name it could not see before.
2. **Add the proxy host.** Forward Hostname is the **Compose service name**
   (`discovery`, not `oan-discovery`, not an IP); Forward Port is the
   **container** port, not what loopback publishes it as. Then SSL, and a DNS
   record before requesting the certificate.

A service **not** in this Compose file needs no step 1 — NPM has egress, so put
its address straight into Forward Hostname. A **second path on an existing
domain** needs no new host either: Custom Locations, one certificate, one DNS
record.

Anything answering unauthenticated needs an **Access List** on top. Discovery
does — `AUTH_ENABLE_SIGNATURE_VERIFICATION` is `false` in this build, so the
edge is the only authentication there is. Check both directions, because the
failure is silent:

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://discovery.oan.example.com/health -u user:pass   # 200
curl -s -o /dev/null -w '%{http_code}\n' https://discovery.oan.example.com/health                # 401
```

### The registry is the one you do not route

It looks like the obvious candidate — `POST /api/v1/Participant/search` takes
no token and is exactly what a peer needs. But SunbirdRC uses POST for reads
*and* writes, so no method rule tells them apart and a proxy host forwards the
whole API. What keeps writes out today is not the route: it is that nothing
outside the VM can mint a Keycloak token, because Keycloak publishes on
loopback. A registry route would depend on that silently.

## Appendix C — Startup order, and why it is that order

```
1. registry and discovery        (also registry-db, keycloak, discovery-db)
2. bin/setup.py                  keys, five participants, two bindings, three configs
3. mock upstreams, then the three adapters
4. nginx-proxy-manager           the public edge — 80 and 443, all interfaces
5. hyperdx                       ClickStack
```

`make up` runs all five; `make up-core` stops after 3, which is enough to
exercise the stack.

**The order is not cosmetic.** An adapter config is a bind-mounted *file*, and
Docker creates a *directory* at any missing bind-mount source — so an adapter
started before step 2 wedges on `adapter.yaml: is a directory` and leaves a
directory where step 2 needs a file. This is why the Makefile exists rather
than a README line saying "run these in order". `setup.py` refuses with an
explanation if it finds one; delete them and re-run.

Re-running is safe: `setup.py` reuses `keys/keys.json` and skips rows that
already exist.

Verify:

```sh
make ps
curl -s -X POST http://127.0.0.1:8081/api/v1/Participant/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' | python3 -m json.tool   # five

curl -s -o /dev/null -w '%{http_code}\n' https://provider.oan.example.com/publish  # 403
```

That 403 is the check worth repeating after **any** NPM change — it is the only
evidence `server_proxy.conf` is still mounted.

## Appendix D — What is in the registry, and why you did not create it

`setup.py` wrote all of it. Nothing here is a step; it is what to look at when
something does not match.

**Three `node` rows, one per adapter** — an id, a role (`consumer`, `provider`,
`network`) and the public halves of a keypair. Private halves stay in
`keys/keys.json` and are never in the registry. Keys are published as bare
base64, no encoding label.

**Two `upstream` rows, one per mock** — an ordinary HTTP API this deployment
calls. It signs nothing, so it needs no role and no keys. Holds a `baseUrl`,
here a Compose service name. No upstream credential lives in the registry
either: the adapter config names *environment variables*, not values.

**Two `ProviderSchema` rows, one per capability** — which upstream answers
which capability and how to call it: method, path, timeout, retries, and the
mapping URL. Its `bindingKey` is `participantId|capabilityCode`:

```
mausamgram-mock|openagrinet:WeatherObservation
agmarknet-mock|openagrinet:MandiPrice
```

Those two strings are the hinge. The provider adapter builds the same key from
each payload — the provider id and the capability `@type` it carries — and a
step answers only when the key matches its own. `setup.py` renders those keys
into `provider.yaml` from the same `.env` it seeds the registry from, which is
what stops the two drifting.

**Looking at it** — from the VM or a tunnel. Search takes no token; writes do,
and the token request has a trap:

```sh
TOKEN=$(curl -s -X POST \
  "http://127.0.0.1:8080/auth/realms/sunbird-rc/protocol/openid-connect/token" \
  -H 'X-Forwarded-Host: keycloak:8080' -H 'X-Forwarded-Proto: http' \
  -d 'client_id=registry-frontend' -d 'grant_type=password' \
  -d 'username=no-user' -d 'password=no-user-password' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Those `X-Forwarded-*` headers are not optional and `keycloak:8080` is the
**container-internal** address on purpose. Keycloak builds the token's issuer
from them and the registry validates it against the internal address; get it
wrong and you get a 401 with an empty body.

**An upstream may carry its own `keys`.** `setup.py` adds none — the mocks have
no keypair — but the schema permits it, and the adapter accepts such a key as a
signer. With one, a provider signs its own catalogue and posts `/publish`
straight at the **network** adapter, which verifies against that row. The
provider adapter drops out of the publish path, and with it the
unauthenticated `/publish` it otherwise has to expose.

## Appendix E — How a request flows

```
discover   you -> exp -> network -> discovery service
select     you -> exp -> provider -> the upstream that owns that capability
publish    a catalogue system -> provider -> network -> discovery service
```

`discover` and `publish` both end at discovery and both go through the network
adapter, which fronts it, verifies the caller and re-signs. `select` never
touches it.

Which upstream answers is in no routing table. The provider adapter runs a
chain of capability steps — `WeatherObservation`, then `MandiPrice` — each
building a binding key from the payload, serving the request if the key is its
own and passing it through untouched if not. So one adapter fronts both
capabilities, and a third is a plugin plus two registry rows, not a new route.

**The action comes from the URL, not the payload.** The adapter strips the
module's mount path and matches what remains — `select`, `discover` — against
the routing config. The schema validator is the exception: it reads
`context.action` from the body and ignores the path. Nothing reconciles the
two, though a mismatch usually fails validation anyway.

Publishing enters at the **provider** adapter, which signs and forwards:

```sh
curl -s -X POST http://127.0.0.1:9200/publish \
  -H 'Content-Type: application/json' -d @your-catalog.json
```

Three things follow from that:

- It is mounted on the **exact path** `/publish` while the Beckn surface takes
  the whole subtree at `/`. Go's mux prefers the exact pattern, so `/select`
  still reaches the capability module. Give both the same path and registration
  panics at startup.
- **Hence `routing-provider.yaml` keys on an empty endpoint.** Stripping
  `/publish` off `/publish` leaves nothing, so the empty string *is* the
  endpoint — which is why `excludeAction: true` and a target URL written out in
  full.
- **The body needs no `bapId`/`bppId` and the caller need not sign.** This
  module verifies nothing inbound; it signs the forwarded request as itself,
  and identity travels in the `Authorization` header's `keyId`. The network
  adapter verifies that.

## Appendix F — When it does not work

**404 `NET_ENTITY_NOT_FOUND`, "no capability matching the request".** No
provider step recognised the payload, so each passed it through and nothing
answered. A step compares a key built from the payload — provider id at
`message.contract.commitments[].offer.provider.id`, capability at
`...resources[].resourceAttributes.@type` — against its own config. Compare the
payload with `.env`, then re-run `setup.py` and recreate the adapter.

**404 naming a binding with no active record.** The other side: a step *is*
configured for the key, but no active `ProviderSchema` row carries it.

```sh
curl -s -X POST http://127.0.0.1:8081/api/v1/ProviderSchema/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' \
  | python3 -c 'import json,sys; [print(r["bindingKey"], r.get("status")) for r in json.load(sys.stdin)]'
```

Compare character for character. The registry is append-only, so a mistyped row
cannot be edited, only superseded under a new id.

**`unrecognized step: <name>` at startup.** Not a config typo. A step name that
is not built in is looked up among loaded plugins, and a plugin's id is the
basename of its `.so` — so `ADAPTER_IMAGE` predates the config. Check what the
image carries, then fix the tag rather than the config → Appendix G.

```sh
docker run --rm --entrypoint sh $ADAPTER_IMAGE -c 'ls plugins/*.so'
```

**502 from a `select`, with an upstream status in it.** Not a binding problem —
the upstream answered non-2xx. 4xx is reported immediately, 5xx retried up to
`retryMax` from the ProviderSchema row. A non-2xx never reaches the mapping.

**Adapters restart in a loop on the first `up`.** Expected before `setup.py`
has run. If it persists, look for a *directory* where a config file should be →
Appendix C.

**`setup.py` says the registry did not come up.** Check `make ps`. Keycloak's
healthcheck allows five minutes on a cold volume.

**`setup.py` says a participant is registered with a different key.** The
registry cannot update a published key and its delete is soft, so the id cannot
be reused. Restore the matching `keys/keys.json`, or pick a new
`*_SUBSCRIBER_ID` in `.env`.

**The registry refuses a write with 401 and an empty body.** The token's issuer
does not match → the `X-Forwarded-*` headers in Appendix D.

**A pull or a mapping fetch fails with "network is unreachable".** DNS returned
an IPv6 address the host cannot route.

**NPM's default page, a 502 that worked yesterday, a failed certificate, or 429
on the experience host** → Appendix B.

## Appendix G — Updating a deployment that is already running

**Config only** — a `.tmpl`, a routing file, `.env`:

```sh
make pull      # git pull, and fixes the ownership NPM leaves behind
make up        # step 2 re-renders the configs, then recreates
```

`make restart` alone is not enough for a `.tmpl` change: adapters read a
rendered `.yaml`, and only `setup.py` writes it.

**A new adapter image as well.** Any change to plugin ids is this case, because
an id is a `.so` basename. If `ADAPTER_IMAGE` names a **new tag**, set it before
`make up`. If it follows **`latest`**, `make up` is not enough —
`pull_policy: missing` means a tag already on disk is never re-fetched and
nothing in `stack.sh` pulls, so the stack quietly comes back on the old image:

```sh
docker compose pull provider-adapter network-adapter exp-adapter
```

Build from the adapter repo at the commit the config expects, and check the
image before deploying it — this is the step that catches a wrong branch:

```sh
docker run --rm --entrypoint sh <image> -c 'ls plugins/*.so'
```

Rebuild **every** adapter image, not one. A partial rebuild presents as a config
typo in one adapter rather than a stale image in the others.

**Payload shapes changed.** If `@context` moved, catalogues already in
discovery carry the old value and `schemaContext` is matched by exact string
equality — so discover alone returns zero. Run the collection top to bottom so
publish reseeds first; `updateMode: MERGE` updates in place.

**Then check, cheapest first:**

```sh
docker compose logs provider-adapter | grep 'Processor steps initialized'
docker compose logs provider-adapter | grep -iE '"level":"(error|fatal)"'
make ps
```

**Rolling back** is `git checkout <old commit>`, `ADAPTER_IMAGE` back to the old
image, `make up` — both together, since the old image with the new config fails
at startup and the new image with the old config runs the old behaviour
silently. Note `latest` serves this badly: "the old image" has no name once the
tag has moved, so recovery is by digest. Pin a tag before a change you might
need to undo.

## Appendix H — Telemetry

`make observability` brings up HyperDX on `127.0.0.1:8085` with OTLP on
4317/4318. It is `clickstack-local`: single-user, no team to create and no
ingestion key to mint, which is what makes it one command — and also why it
must stay on loopback, since there is no login in front of it.

**Less arrives than the wiring suggests**, which is worth knowing before
hunting for absent traces. Discovery reads the OTLP variables but nothing in
the current build consumes them, so `OTEL_EXPORTER` stays `none`. Whether the
adapter image's SDK reads them is unverified — nothing depends on the answer,
since an absent collector makes an exporter drop spans rather than fail a
request. And container logs go nowhere near HyperDX without a collector with a
`filelog` receiver, which is not in this stack; `docker compose logs -f`
remains the way to read them.

Treat this profile as the destination being ready, not as observability being
switched on.

## Appendix I — Schema validation

Every adapter validates request bodies against the pinned Beckn v2 LTS spec. On
the **provider adapter** a second layer runs too: it walks the payload for
objects carrying `@context` and `@type`, resolves the schema `@type` names, and
validates against it. Base validation treats `resourceAttributes` as free-form,
so this is the only layer checking a capability's own attributes.

The schemas are neither committed nor mounted. `@context` names the published
pack and the validator swaps `context.jsonld` for `attributes.yaml`:

```
@context   .../network-specs/schema-packs-v0.1/schema/MandiPrice/v0.1/context.jsonld
fetched    .../network-specs/schema-packs-v0.1/schema/MandiPrice/v0.1/attributes.yaml
```

So a payload names the revision it is judged against, and no copy here can
drift. Cached 24h, so only the first payload after a restart pays. Two
consequences: the adapter needs egress to `raw.githubusercontent.com`, and a
**failed fetch rejects the payload** rather than skipping validation. An
`@context` on any other host is refused before any fetch —
`extendedSchema_allowedDomains` is the list.

**What it does not check: `if`/`then`/`else`.** The validator library parses
those keywords and never evaluates them, so every pack rule predicated on
`informationMode` is unenforced — a pass here is not pack conformance. It does
enforce types, string formats, `enum`, `const`, `required`,
`additionalProperties`, `not` and `allOf`/`anyOf`/`oneOf`.

Three things that bite when writing a payload:

- **Every resource under a commitment needs a `quantity`.** The spec requires
  it while defining no `Quantity` schema at all — a defect upstream. Any value
  satisfies it; without one every `select` is refused with
  `SCH_REQUIRED_FIELD_MISSING`.
- **A `date-time` field will not take a bare date.** `validity.startsAt`/
  `endsAt` are `format: date-time`, so `2025-08-20` is refused and
  `2025-08-20T00:00:00+05:30` accepted. `arrivalDate` is `format: date` and
  wants the opposite.
- **`publish` is validated on the provider adapter**, because `validateSchema`
  is in that module's `steps:` — declaring a validator is not enough, a plugin
  missing from `steps:` never runs. The network adapter validates nothing: its
  single module is `validateSign`, `addRoute`, `sign`.

## Appendix J — About the mapping files

`config/mappings/` holds the two this deployment uses, and `MAPPING_URL` /
`MANDI_MAPPING_URL` point at **this repo's own copies** over GitHub's raw CDN —
so the file a reader reviews and the file the adapter fetches are one file.

Each has two halves: the request half turns the Beckn payload into what the
upstream expects, the response half turns the answer into resources. The mandi
one shows why this is not field renaming — ISO dates to `dd-MM-yyyy`,
`marketcode` sent only when the request carried one, price strings to numbers,
an unreported price omitted rather than sent as zero.

It is a URL rather than a path because the registry publishes the full URL and
the adapter fetches it verbatim — so a mapping must be reachable before it can
be tested, and this stack exercises exactly what a consumer fetches.

**Note the branch in those URLs.** Once this merges, point them at the default
branch or pin a tag.

A real upstream answering with different field names, a different date format
or a nested envelope is a mapping edit and a cache expiry — no code. What is
*not* fixable here is anything depending on a response that never arrives: a
non-2xx fails the step first. Allow a few minutes for an edit to appear —
one minute of adapter cache plus about five of CDN.

## Appendix K — The layout

```
docker-compose.yml          the whole stack, in tiers -- the banner comments
                            are the structure
.env.example                copy to .env
Makefile                    the front door; every target delegates to stack.sh
bin/
  bootstrap-ubuntu.sh       docker and python on a fresh Ubuntu VM
  stack.sh                  the startup order, and why it is that order
  setup.py                  keys, five registry rows, the adapter configs
config/
  reverse-proxy/
    npm-custom/             mounted to /data/nginx/custom; NPM includes these
      http_top.conf           on its own -- rate-limit zone, and the /publish
      server_proxy.conf       deny every proxy host gets
    npm-advanced/exp.conf   NOT loaded. Paste into the Advanced tab; kept here
                            because a textarea in a database is not reviewable
  adapters/
    experience.yaml.tmpl    templates. setup.py renders these to .yaml,
    network.yaml.tmpl       filling in the keys it generated. The rendered
    provider.yaml.tmpl      files hold private keys and are gitignored
    routing-experience.yaml which action goes where: experience sends discover
    routing-network.yaml    to the network layer and select to the provider;
    routing-provider.yaml   provider sends publish to the network layer
  registry/
    schemas/                Participant, ProviderSchema, SchemaRegistry.
                            Read at startup -- a change needs a restart
    imports/                the Keycloak realm
  discovery/                optional instance override
  mappings/                 one file per binding-action, served over the raw CDN
../postman-collection/      the collection and its environment file
```

## Appendix L — Renaming this directory

Compose takes its **project name from the directory holding the compose file**,
and every named volume is prefixed with it. So renaming this directory renames
all five volumes, and **Docker does not move the data**: a plain `make up`
afterwards starts on an empty registry, an empty catalogue, and an NPM with no
proxy hosts and no certificates. The old volumes are orphaned, not gone.

`npm-letsencrypt` is the one to care about — re-issuing runs into Let's
Encrypt's duplicate limit, five per week for the same names.

Copy the data across before starting. Stop the stack first, from whichever
name it is running under:

```sh
make down
for v in registry-data discovery-data npm-data npm-letsencrypt hyperdx-data; do
  docker volume create "quick-start_$v" >/dev/null
  docker run --rm -v "old-name_$v:/from" -v "quick-start_$v:/to" alpine \
    sh -c 'cd /from && tar cf - . | (cd /to && tar xf -)'
done
```

Then `make up`, and confirm the five participants and your proxy hosts before
deleting anything. Keycloak shares `registry-data`, so its realm travels with
that volume — and equally does not survive if you skip it.

## Appendix M — Starting over

```sh
docker compose down -v
rm -rf keys config/adapters/experience.yaml config/adapters/network.yaml \
       config/adapters/provider.yaml
```

New keys mean new identities, so the provider rows must be created again and
**the old participant ids cannot be reused** — the registry's delete is soft
and keeps the unique index.

`-v` deletes **every** volume, including `npm-letsencrypt` and `npm-data` —
your certificates and your whole routing table. To clear only catalogues, drop
`quick-start_discovery-data` alone and leave the rest.

## Appendix N — What the collection demonstrates

**The two `select` requests are the pair worth comparing.** Same endpoint, same
adapter, and different domain packages answer them — because each provider step
builds a binding key from the payload, serves the request if the key is its own
and passes through anything else. Nothing routes by URL, path or domain. That
is the whole dispatch mechanism, and these two requests are what show it.

**`networkAdapterUrl` is a variable no request uses, on purpose.** `discover`
reaches the network adapter through the experience adapter and `publish`
through the provider adapter, so nothing in the collection calls it directly.
It is listed because it is the other adapter a deployment exposes publicly —
its `/publish` and `/discover` both verify signatures, so a network peer calls
it directly. Signing is not something Postman does, so those calls are not
scripted. The variable exists to give the address somewhere to live, not
because a request is missing.
