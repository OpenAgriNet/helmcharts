# OAN stack, on Docker Compose

The whole OpenAgriNet stack for a **dev deployment on a VM**: the registry, the
discovery service, and the three adapters. One compose file, one config folder.

This is a dev environment. It is not production: the adapter signing keys sit
in a config file on disk, nothing terminates TLS, and every credential shipped
in `.env.example` is a public default.

## What is here, and what is not

Running here:

- **registry** — SunbirdRC, plus its Postgres and Keycloak. Holds who is on the
  network, their public keys, and which upstream API answers which capability.
- **discovery** — catalogue search, plus its own Postgres.
- **three adapters** — experience, network and provider. Same image, three
  configs.
- **gateway** — Nginx Proxy Manager, the only container that publishes on a
  routable interface. Routes to the three adapters, and issues and renews the
  Let's Encrypt certificates from its own UI. Profile `gateway`.
- **hyperdx** — ClickStack: OTLP ingest, ClickHouse, and the UI over it.
  Profile `observability`.

The last two are behind profiles because neither is needed to exercise the
stack, and HyperDX is the heaviest thing here.

Deliberately **not** here:

- **the provider API.** Whoever is testing runs it themselves and gives it a
  URL the VM can reach. The provider adapter never has that address in a config
  file — it reads it from the registry per request, so repointing it is a
  registry write and nothing more.
- **the provider's registry rows.** Those two are created by hand, because the
  base URL belongs to whoever runs the API. `bin/setup.py` registers only the
  three adapters.

## Reaching it

Two doors, and which one you use depends on what you are reaching.

### The adapters — through Nginx Proxy Manager

NPM owns 80 and 443 and is the whole public surface. Unlike a config file, its
routing table is **rows in a SQLite database** inside the `npm-data` volume —
so the setup below is a one-time click-through, and that volume is the only
copy of the result. Back it up.

**First boot.** Tunnel to the admin UI (it is bound to loopback on purpose,
see below) and change the shipped login immediately:

```sh
ssh -L 81:127.0.0.1:81 -N you@the-vm
```

Open `http://127.0.0.1:81`. It logs in with `admin@example.com` / `changeme`,
which is live from first boot until you change it, and it forces a change on
first use. Do that before creating anything.

**Then one proxy host per adapter.** Hosts → Proxy Hosts → Add Proxy Host:

| Domain | Forward Hostname | Port | Then |
|---|---|---|---|
| `exp.oan.example.com` | `exp-adapter` | 9202 | paste `config/gateway/npm-advanced/exp.conf` into **Advanced** |
| `network.oan.example.com` | `network-adapter` | 9201 | — |
| `provider.oan.example.com` | `provider-adapter` | 9200 | — |

Scheme `http` for all three: TLS terminates at NPM, and the hop to an adapter
is inside `oan-edge`. Turn on **Block Common Exploits**; leave **Websockets
Support** off, since nothing here uses them.

Three hosts rather than one host with path prefixes, because a rate limit or a
block then attaches to a whole hostname instead of being expressed as a regex
in a textarea — and each gets its own certificate. A request arriving with a
`Host` NPM does not know gets NPM's default page, not an adapter.

**Certificates.** SSL tab → Request a new SSL Certificate → Force SSL → HTTP
Validation. That needs two things to be true, and both are easy to miss:

- a public DNS **A record** per hostname, pointing at the VM's address — an
  Elastic IP, unless you enjoy redoing this after every stop/start;
- **port 80 open to `0.0.0.0/0`**, not to your address. Let's Encrypt fetches
  `http://<name>/.well-known/acme-challenge/…` from its own servers, whose
  addresses you do not get to enumerate. A security group scoped to your IP
  makes issuance fail with a challenge timeout, which looks nothing like a
  firewall problem in the NPM log.

If 80 must stay closed, use **DNS Validation** instead: NPM ships the certbot
Route 53 plugin, so you give it an access key with `route53:ChangeResourceRecordSets`
on the zone and it never needs an inbound request. That is the better answer on
AWS anyway, and it is the only one that works for a wildcard.

Renewal is NPM's job from then on, and it uses the same validation method — so
a DNS record or an SG rule that was only temporarily correct will fail silently
in sixty days.

### What is *not* reachable, and why that holds

`POST /publish` returns 403 on all three hosts. This is not optional
hardening — it is the one thing standing between the public internet and an
unauthenticated write into the catalogue.

The provider adapter mounts two modules: `/oan/` verifies the sender's
signature against the registry, but `oanProviderPublish` is mounted at `/` with
**no signature check at all**, because its intended caller is the provider's
own catalogue system inside the trust boundary. A proxy host pointed at
`provider-adapter:9200` therefore exposes `<host>/publish` to anyone. NPM's UI
offers no way to route a host while withholding one path, so the block lives in
`config/gateway/npm-custom/server_proxy.conf`, which NPM includes in **every**
proxy host's server block automatically — a mounted file, not a click, and so
not something to remember on one host out of three.

To let a real catalogue system publish, give it a tunnel or put it in the VPC
and let it reach `provider-adapter:9200` directly. Do not turn that `deny` into
an `allow`: an endpoint with no credential to check does not belong on a public
edge, and "an address allowlist in front of it" is a statement about the
network, which is where it should be made.

And the deeper reason a UI-configured proxy is safe here at all: **NPM sits on
`oan-edge` only.** "Forward Hostname" is a free-text field, so anyone with the
admin password can type `registry`, `keycloak` or `discovery-db` into it — and
on that network none of those names resolve and none of those addresses are
routable. The blast radius of a wrong click is bounded to the tier that is
public anyway. Moving NPM onto `oan-internal` to "make things easier" would
remove that bound and put the registry's write API one form field away from the
internet.

### Which nginx config is loaded, and which is a paste job

Worth being exact about, because the two look alike in the repo:

| File | How it applies |
|---|---|
| `config/gateway/npm-custom/http_top.conf` | **Automatic.** NPM includes it at the top of its `http` block. Declares the `exp` rate-limit zone and `limit_req_status 429`. |
| `config/gateway/npm-custom/server_proxy.conf` | **Automatic.** Included in every proxy host's server block. Holds the `/publish` deny. |
| `config/gateway/npm-advanced/exp.conf` | **Manual.** Paste into the experience host's Advanced tab. Applies `limit_req` to that host only, since a 10 r/s ceiling on signed peer traffic would throttle for no security gain. |
| `config/gateway/npm-advanced/registry.conf` | **Manual.** Paste into the registry host's Advanced tab, if you create one. Reduces the host to the two search endpoints and 403s the rest. |

The manual one is in a file anyway because NPM's Advanced field is a textarea
in a database row: nothing diffs it and nothing reviews it. Keeping the source
here means the rule can be read even though the running copy cannot.

### Adding a route for another service

Deliberately two steps, and the first one is in git rather than in the UI.

NPM is on `oan-edge`, where only the three adapters resolve. A proxy host
pointed at `registry` or `hyperdx` does not quietly work — it 502s, because
there is no route. So publishing something new is a change to
`docker-compose.yml` that a reviewer sees, followed by a click. The UI alone
cannot widen what is public. That is the property worth keeping; everything
below is about how to spend it deliberately.

**Step 1 — put the service on `oan-edge`.** In `docker-compose.yml`, add the
network to that service. Keep `oan-internal` if it talks to anything else in
the stack, and keep the loopback publish or drop it as you like — NPM reaches
the container port directly, not the published one:

```yaml
  some-service:
    networks: [oan-internal, oan-edge]
```

Then `docker compose up -d some-service nginx-proxy-manager`. NPM needs the
restart to pick up a name it could not resolve before.

**Step 2 — add the proxy host.** UI → Hosts → Proxy Hosts → Add Proxy Host.
Domain `some.oan.example.com`, scheme `http`, Forward Hostname the **compose
service name** (`some-service`, not `oan-some-service` and not an IP), Forward
Port the **container** port. Then the SSL tab as with the adapters, and a DNS
A record before you request the certificate.

**A service that is not in this compose file** — a provider API on another
host, something in the VPC — needs no step 1 at all. NPM has egress, so put
its address or hostname straight into Forward Hostname. Nothing about the
network split is involved, and nothing about that service becomes reachable
from inside this stack.

**A second path on an existing domain** does not need a new host either. Open
the host → Custom Locations → add e.g. `/v2` forwarding to another service.
That keeps one certificate and one DNS record, at the cost of NPM's generated
config growing a location block you cannot see in the UI's main view.

#### Worked example: the registry

`registry` is already on `oan-edge` in `docker-compose.yml`, so step 1 is
done — but read the comment there before you use it, because the mechanics are
the easy part.

**What is actually reachable once you route it.** `POST /api/v1/Participant/search`
takes no token; that is the call a peer needs, and publishing it is defensible.
Everything else under `/api/v1/` is a write, and writes need a Keycloak token.
Those are unobtainable from outside **today for one reason only**: Keycloak
publishes on `127.0.0.1`. The safety of this route therefore rests on a
decision made elsewhere in the compose file. Publish Keycloak later and the
registry's write surface opens along with it, with nothing on this host
changing to say so.

There is a second wrinkle even for someone who has a token. The registry
validates a token's issuer against `http://keycloak:8080/auth/realms/…`, the
**container-internal** address, which is why the token request further down
this README carries `X-Forwarded-Host: keycloak:8080`. A token minted through
any other hostname is rejected with a 401 and an empty body. So "it 401s
through the proxy but works over the tunnel" is expected, not a proxy bug.

**Create the host.** Hosts → Proxy Hosts → Add:

| Field | Value |
|---|---|
| Domain | `registry.oan.example.com` |
| Scheme | `http` |
| Forward Hostname | `registry` — the compose service name, not `oan-registry` |
| Forward Port | `8081` — the **container** port. Not `REGISTRY_PORT`, which is only what loopback publishes it as |
| Block Common Exploits | on |

**Then both guards, before you point anything at it.**

1. Advanced tab → paste `config/gateway/npm-advanced/registry.conf`. That
   reduces the host to `Participant/search` and `ProviderSchema/search` and
   403s everything else. It is an allowlist rather than a list of things to
   block, because SunbirdRC uses POST for both search and create — no method
   rule separates a read from a write, so a denylist is a list someone has to
   keep complete forever.

2. Access Lists → Add, then assign it on the host's Details tab. **Satisfy Any
   off**, so an address *and* a password are needed. The path filter is not
   authentication: search returns the full participant list — public keys,
   baseUrls, who is on this network — to anyone who reaches it.

**Check it does what you think:**

```sh
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://registry.oan.example.com/api/v1/Participant/search \
  -u user:pass -H 'Content-Type: application/json' -d '{"filters":{}}'   # 200

curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://registry.oan.example.com/api/v1/Participant \
  -u user:pass -H 'Content-Type: application/json' -d '{}'               # 403

curl -s -o /dev/null -w '%{http_code}\n' \
  https://registry.oan.example.com/api/v1/Participant/search             # 401
```

403 on the second is the Advanced paste; 401 on the third is the Access List.
If either returns 200, one of the two guards is not attached — and the failure
is silent, so this is worth re-running after any NPM change.

If you decide against the route, take `oan-edge` back off `registry` in
`docker-compose.yml` rather than only deleting the proxy host. An attached
service is one form field away from being public.

#### Before you route the ones already here

Three of the internal services will look like obvious candidates. They are
not equivalent:

| | What routing it publishes |
|---|---|
| **discovery** | Read-mostly catalogue search. The most defensible of the three, and still: it answers unauthenticated, and `AUTH_ENABLE_SIGNATURE_VERIFICATION` is `false` with nothing behind it in this build. Put an Access List on it. |
| **registry** | The network's identity records. Reads are unauthenticated, writes need a Keycloak token. Publishable, but only cut down to the search endpoints and behind an Access List — see below. |
| **keycloak** | An admin console with a realm imported from a file that ships `no-user` / `no-user-password` and an admin-api client secret. Do not publish it. |
| **hyperdx** | `clickstack-local` runs single-user with **no login at all**. Publishing it hands over every trace and log the stack has collected. If it must be shared, switch to `clickstack-all-in-one` and set up a team first. |
| **registry-db, discovery-db** | No. Use `docker compose exec`, or a tunnel. |

The pattern: publishing a service that has no authentication of its own means
the edge is now its only authentication. NPM can be that, but only if you say
so explicitly.

#### Putting authentication in front of one

NPM's **Access Lists** are the built-in answer, and they are per-host: UI →
Access Lists → Add. Two independent tabs —

- **Authorization**: username/password pairs, enforced as HTTP basic auth.
- **Access**: `allow`/`deny` rules by address or CIDR.

**Satisfy Any** decides how they combine, and the default is the one people
get wrong. *Any* means an allowed address gets in without a password — fine
for "the office network, or a password from anywhere". Turn it **off** for
"an allowed address **and** a password", which is what you want in front of
anything that has no auth of its own.

Then assign the list on the proxy host's Details tab. It applies to the whole
host, including any Custom Locations under it.

Basic auth is not a substitute for a real access control, and it travels in a
header on every request — so it is worth having only over HTTPS, which is the
other reason to get the certificate before the route.

#### The cost of each addition

Every service you attach to `oan-edge` is one form field away from being
public, because that is exactly what the network split buys and spending it is
irreversible by clicking. Keeping `oan-edge` small is what keeps "someone got
into the NPM admin UI" a bounded incident rather than an open question about
the registry.

So: add the network in the same change that adds the proxy host, not in
advance "so it's ready". And when a route is retired, take the service back
off `oan-edge` rather than only deleting the host in NPM.

### Everything else — through an SSH tunnel

The registry, Keycloak, discovery, the HyperDX UI and NPM's own admin UI
publish on `127.0.0.1` only:

```sh
ssh -L 81:127.0.0.1:81 \
    -L 8080:127.0.0.1:8080 \
    -L 8081:127.0.0.1:8081 \
    -L 8082:127.0.0.1:8082 \
    -L 8085:127.0.0.1:8085 \
    -N you@the-vm
```

NPM admin on 81, Keycloak on 8080, the registry on 8081, discovery on 8082,
HyperDX on 8085 (adjust to your `.env`). Both Postgres instances publish no
port at all; reach them with `docker compose exec registry-db psql …`.

The admin UI is on loopback rather than published-and-firewalled, which is what
the port comment in most NPM compose examples suggests. It ships with a known
default login and it is the one surface on this box that can mint certificates
and re-point every public route; a security group is a second system to keep in
step with that, and a loopback bind is not.

There is no `BIND_ADDR` any more. It used to move every published port onto the
public interface at once, which is a footgun once something exists to expose
the one tier that should be reachable.

## Before you start

On the VM:

- Docker with Compose **v2.24 or newer**, logged in to wherever the images live
  if it is private — `docker login ghcr.io`. The version floor is the
  `env_file: required: false` on the HyperDX service, which is what lets an
  absent `.env.docker` be absent instead of fatal.
- Python 3 and the `cryptography` package — `pip install cryptography`
- 16 GB of RAM if you run the `observability` profile — ClickHouse alone wants
  2-4 GB on top of the two JVM services. 8 GB is workable without it.

And a URL the VM can reach for the upstream provider API. If that API runs on
someone's laptop, [ngrok](https://ngrok.com/) or any equivalent tunnel gives it
one.

## Bring it up

```sh
cp .env.example .env
```

Read `.env` before going on. Four things in it matter:

- `TAG` — pins the discovery service. Unset means `latest`; set it to deploy a
  known build instead of whatever `latest` points at today:
  `TAG=v0.3.1 docker compose up -d`. The images themselves are already named in
  `.env.example` and are pulled, never built.
- **the credentials.** All shipped defaults. Change them.
- `BIND_ADDR` — see above.
- `PROVIDER_PARTICIPANT_ID` and `PROVIDER_CAPABILITY` have to match the registry
  rows created further down.

Then:

```sh
# 1. everything EXCEPT the adapters. Their configs do not exist yet, and
#    step 2 is what writes them.
docker compose up -d registry discovery

# 2. generate the adapter keypairs, register the three adapter identities,
#    render the three adapter configs
python3 bin/setup.py

# 3. now the adapters
docker compose up -d

# 4. the edge and the telemetry stack, both opt-in
docker compose --profile gateway --profile observability up -d
```

**Do not run a bare `docker compose up -d` for step 1.** An adapter config is
a bind-mounted *file*, and Docker creates a *directory* at any bind-mount
source that is missing. Starting an adapter early therefore wedges it on a
directory it cannot parse — `adapter.yaml: is a directory` — and leaves a
directory where step 2 needs to write a file. `bin/setup.py` refuses with an
explanation if it finds one; delete the empty directories and re-run.

Check it:

```sh
docker compose ps
curl -s -X POST http://127.0.0.1:8081/api/v1/Participant/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' | python3 -m json.tool
```

Three participants, one per adapter. That is what `setup.py` seeded.

And through the gateway, once the proxy hosts exist:

```sh
# the routed surface
curl -s -o /dev/null -w '%{http_code}\n' \
  https://exp.oan.example.com/oan/search        # reaches the adapter

# the two that matter more
curl -s -o /dev/null -w '%{http_code}\n' \
  https://provider.oan.example.com/publish      # 403 -- the deny is loaded
curl -s -o /dev/null -w '%{http_code}\n' \
  http://the-vm-ip/                             # NPM default page, no adapter
```

That 403 is the check worth repeating after any NPM change: it is the only
evidence that `npm-custom/server_proxy.conf` is still mounted, and losing the
mount silently opens an unauthenticated catalogue write.

## Register the provider

**Quickest path: import `postman-collection/`.** It does everything in this
section and the end-to-end test after it — a token, the provider's two rows,
both registry searches, publish, discover and select — with every value
prefilled to match this deployment. Set one variable, `upstreamBaseUrl`, to a
URL the VM can reach for your API, and run the requests in order.

The rest of this section is the same thing as curl, if you would rather see it
step by step. Two rows, both by hand, and both need a token.

Get the upstream API's URL first. If it is tunnelled from a laptop:

```sh
ngrok http 9100
```

Take the `https://` URL. Then get a token:

```sh
TOKEN=$(curl -s -X POST \
  "http://127.0.0.1:8080/auth/realms/sunbird-rc/protocol/openid-connect/token" \
  -H 'X-Forwarded-Host: keycloak:8080' -H 'X-Forwarded-Proto: http' \
  -d 'client_id=registry-frontend' -d 'grant_type=password' \
  -d 'username=no-user' -d 'password=no-user-password' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Those two `X-Forwarded-*` headers are not optional, and `keycloak:8080` is the
**container-internal** address on purpose — not whatever `KEYCLOAK_PORT` is
published as. Keycloak builds the token's issuer from these headers, and the
registry validates that issuer against the internal address. Get it wrong and
the registry rejects the token with a 401 and an empty body.

**Row one — the API itself.** Type `upstream`: an ordinary HTTP API this
deployment calls. It does not sign anything and nothing verifies it, so no
keys are needed — the signing in this flow is between adapters, on the three
`node` identities `bin/setup.py` seeded. `role` and `keys` are accepted on an
upstream if a deployment wants to record them; nothing reads them.

```sh
curl -s -X POST http://127.0.0.1:8081/api/v1/Participant \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{
    "participantId": "my-weather-api",
    "name": "My weather API",
    "type": "upstream",
    "status": "active",
    "baseUrl": "https://YOUR-TUNNEL-SUBDOMAIN.ngrok-free.app"
  }'
```

**Row two — which capability it answers, and how to call it.**

```sh
curl -s -X POST http://127.0.0.1:8081/api/v1/ProviderSchema \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{
    "bindingKey": "my-weather-api|openagrinet:WeatherObservation",
    "participantId": "my-weather-api",
    "capabilityCode": "openagrinet:WeatherObservation",
    "status": "active",
    "actions": [{
      "action": "select",
      "method": "GET",
      "path": "/get-daily",
      "mappings": "https://raw.githubusercontent.com/OpenAgriNet/helmcharts/feat/4-docker-compose/docker-deployment/config/mappings/mausamgram/weather-observation.select.yaml",
      "timeoutMs": 15000,
      "retryMax": 2,
      "status": "active"
    }]
  }'
```

Things worth knowing about these two calls:

- **No `{"Participant": {...}}` wrapper.** The registry takes the record
  itself. A wrapper comes back as `extraneous key [Participant] is not
  permitted`.
- **An `upstream` needs no `role` and no `keys`**, and no credential is held
  for it here. It has never heard of Beckn, and nothing in the registry is
  sent to it — the adapter presents credentials from its own config, naming
  environment variables. `role` and `keys` are permitted if a deployment wants
  to record them, but nothing reads them: a signature is verified against the
  `node` identity that signed it.
- **The three roles are `consumer`, `provider` and `network`**, and they apply
  to `node` rows only — the three `setup.py` creates. A node also needs at
  least one key, published as bare base64 with no encoding label in front of
  it.
- **`bindingKey` is `participantId|capabilityCode`.** It has to match what
  `PROVIDER_PARTICIPANT_ID` and `PROVIDER_CAPABILITY` were set to in `.env`
  when `setup.py` last ran — see the troubleshooting section for what a
  mismatch answers.
- **`path` must start with one `/` and contain no empty segment.** The schema
  refuses `//get-daily`, and so does the adapter.
- **This registry is append-only.** There is no update, delete is soft, and a
  soft-deleted id keeps the unique index — so an id can never be reused. Got a
  row wrong? Pick a new id.

## Test it end to end

Replace `my-weather-api` if a different id was used, and point the coordinates
at wherever the API has data.

```sh
curl -s -X POST http://127.0.0.1:9202/oan/select \
  -H 'Content-Type: application/json' \
  -d '{
    "context": {
      "version": "2.0.0", "action": "select",
      "networkId": "oan-dev",
      "transactionId": "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
      "messageId": "7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
      "timestamp": "2026-09-02T06:12:01.330Z"
    },
    "message": { "contract": { "commitments": [ {
      "status": { "descriptor": { "code": "DRAFT", "name": "Draft" } },
      "resources": [ {
        "id": "res:point-forecast",
        "quantity": 1,
        "resourceAttributes": {
          "@context": "https://schemas.openagrinet.global/schema/WeatherObservation/v0.1/context.jsonld",
          "@type": "openagrinet:WeatherObservation",
          "subjectCategories": ["Weather"],
          "location": { "type": "Point", "coordinates": [73.7898, 19.9975] }
        }
      } ],
      "offer": {
        "id": "offer:open-data",
        "resourceIds": ["res:point-forecast"],
        "provider": { "id": "my-weather-api",
                      "descriptor": { "code": "MY-API-01", "name": "My weather API" } }
      }
    } ] } }
  }' | python3 -m json.tool
```

You should get an `on_select` back, with one resource per forecast day.

No party is named in the payload, in either direction. Identity travels in
the `Authorization` header's `keyId`, which names the signer and the key the
registry published for it; a body that declares no caller simply skips the
declared-identity comparison. Nothing needs `bapId` or `bppId`, and the
`*Uri` fields they came with were container-internal addresses that meant
nothing outside this compose network anyway.

The experience adapter is the only one that takes an unsigned request — the
experience app is inside the trust boundary, so there is no network signature
to check. That is what makes this testable with a plain curl.

## Telemetry

`docker compose --profile observability up -d` brings up HyperDX on
`127.0.0.1:8085` (tunnel to reach it) with OTLP on 4317/4318. It is
`clickstack-local`, not `clickstack-all-in-one`: local runs single-user with no
team to create and no ingestion key to mint, which is what makes `up -d` the
whole setup step — and also why it must stay on loopback, since there is no
login in front of it.

**What actually arrives today is less than the wiring suggests, and that is
worth knowing before you go looking for traces that are not there.**

- **discovery** reads `OTEL_EXPORTER` and `OTEL_EXPORTER_OTLP_ENDPOINT` into
  its config, and nothing in the current build consumes them — the only
  OpenTelemetry packages in its `go.mod` are indirect. So `OTEL_EXPORTER`
  stays `none` by default; setting it to `otlp` emits nothing rather than
  failing. When the exporter is wired, `OTEL_EXPORTER=otlp` in `.env` is the
  whole change and the endpoint already points here.
- **the three adapters** get `OTEL_EXPORTER_OTLP_ENDPOINT` and
  `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`. Whether that image's SDK reads
  them is unverified in either direction — the image is pulled and its source
  is not in this repo. Nothing depends on the answer: an absent collector makes
  an OTLP exporter drop spans, not fail a request.
- **container logs go nowhere near HyperDX** without something to ship them.
  `docker compose logs -f <service>` remains the way to read them. Shipping
  them would mean an OTel collector with a `filelog` receiver over
  `/var/lib/docker/containers`, which is not in this stack.

So treat this profile as the destination being ready and in one place, rather
than as observability that is switched on.

## How a request flows

Three paths, and which adapter answers is the whole design:

```
discover   you -> exp -> network -> discovery service
select     you -> exp -> provider -> your upstream API
publish    your catalogue system -> provider -> network -> discovery service
```

`discover` and `publish` both end at the discovery service, and both go
through the network adapter — that adapter is what fronts discovery, verifies
the caller and re-signs. `select` never touches it: it goes straight to the
provider adapter, which answers from your upstream API.

Each adapter's Beckn surface is one subtree, `/oan/`, and the payload's
`action` says which action it is. That is the path the registry publishes as
a participant's `baseUrl`, so a peer calling `<baseUrl>/select` lands on the
module that answers select.

Publishing enters at the **provider** adapter, which signs and forwards:

```sh
curl -s -X POST http://127.0.0.1:9200/publish \
  -H 'Content-Type: application/json' -d @your-catalog.json
```

Three things about that:

- **`/publish` sits outside `/oan/`, at the root.** It is not part of this
  adapter's Beckn surface — it arrives from inside the provider's own
  deployment — so it must not shadow it. Go's mux takes the longest matching
  pattern, so `/oan/select` still reaches the capability module and only
  `/publish` falls to the root one.
- **It is a second module, and has to be.** The routing step fails any action
  missing from its config, so routing publish from the module that answers
  select would mean listing select too — and listing select would proxy it to
  the network layer instead of answering it there.
- **The catalogue body needs no `bapId` or `bppId`, and the caller need not
  sign.** The provider's own catalogue system is inside its trust boundary,
  so this module verifies nothing on the way in; it signs the forwarded
  request as itself, and identity travels in the `Authorization` header's
  `keyId` from its `keyManager` config. The network adapter verifies that
  signature — and its identity check skips a body that declares no caller
  rather than demanding one.

## Schema validation

Every adapter loads the pinned Beckn v2 LTS spec and validates request bodies
against it. The **extended** layer is off: it fetches each resource's own
`@context` and validates against that, which is a network call per payload and
a second thing that can fail. The `extendedSchema_*` settings in the configs
only take effect if it is switched on.

Two consequences worth knowing before you write a payload:

- **Each resource under a commitment needs a `quantity`.** The spec's
  `Commitment.resources` requires `["id", "quantity"]` while `Resource` itself
  defines no `quantity` property and the spec has no `Quantity` schema at all —
  a defect upstream, not something this deployment chose. Any value satisfies
  it. Without one, every `select` is refused with
  `SCH_REQUIRED_FIELD_MISSING: property "quantity" is missing`.
- **`publish` is not validated, because the spec does not define it.** The
  validator refuses an action it cannot find with `unsupported action: publish`,
  so the two modules that carry publishing — the provider adapter's root mount
  and the network adapter — declare the validator but do not run it. To
  validate publishing, give the validator an auxiliary spec that defines the
  action: `auxiliaryTypes` and `auxiliaryLocations`, which are additive and
  must not overlap the primary spec.

An action the spec does not know, or a body missing a required field, comes
back as a signed NACK with a `SCH_*` code and the JSON path that failed.

## The layout

```
docker-compose.yml          the whole stack. Read it in tiers -- the banner
                            comments are the structure: registry, discovery,
                            adapters, observability (profile), edge (profile)
.env.example                copy to .env
bin/setup.py                keys, the three adapter rows, the adapter configs
config/
  gateway/
    npm-custom/             mounted to /data/nginx/custom, which NPM includes
      http_top.conf         on its own: the rate-limit zone declaration,
      server_proxy.conf     and the /publish deny that every proxy host gets
    npm-advanced/
      exp.conf              NOT loaded -- paste into the experience host's
                            Advanced tab. Kept here because a textarea in
                            NPM's database is not reviewable.
                            The routing table itself is not a file: it is
                            rows in the npm-data volume.
  adapters/
    exp.yaml.tmpl           templates. setup.py renders these to .yaml,
    network.yaml.tmpl       filling in the keys it generated. The rendered
    provider.yaml.tmpl      files hold private keys and are gitignored.
    routing-exp.yaml        which action goes where. exp sends discover to
    routing-network.yaml    the network layer and select to the provider;
    routing-provider.yaml   provider sends publish to the network layer;
                            network sends discover and publish to discovery
  registry/
    schemas/                Participant, ProviderSchema, SchemaRegistry.
                            Read at startup -- a change needs the registry
                            service restarted.
    imports/                the Keycloak realm
  discovery/
    instance.yaml.example   optional override; see the compose file
  mappings/
    mausamgram/             the request and response transformation
postman-collection/         the whole flow as a Postman collection, with the
                            deployment's own values prefilled
```

## About the mapping file

`config/mappings/` holds the mapping this deployment uses, and `MAPPING_URL`
points at **this repo's own copy** over GitHub's raw CDN. So the file a reader
reviews and the file the adapter fetches are one file, and cannot drift.

It is a URL rather than a path because the registry publishes the full URL and
the adapter fetches it verbatim — which means a mapping has to be reachable
before it can be tested, and what this stack exercises is exactly what any
consumer fetches.

Note the branch in that URL. Once this merges, point it at the default branch,
or pin a tag so a deployment is not following a moving file.

To change the mapping: edit the file here and push, or publish a fork anywhere
that serves raw text over https and put that URL in the `mappings` field of
the ProviderSchema row.

The adapter caches a mapping for `cacheTTL` (one minute, in the adapter config)
and GitHub's raw CDN caches for about five, so give an edit a few minutes to
show up.

## When it does not work

**404 `NET_ENTITY_NOT_FOUND`, "this module serves no capability matching the
request".** The commonest one. The provider adapter did not recognise the
request as its own, so it passed it through and nothing behind it answered.

It decides that by building a binding key from the incoming payload — the
provider id at `message.contract.commitments[].offer.provider.id` and the
capability at `...resources[].resourceAttributes.@type` — and comparing it
against the keys in its own config, which `setup.py` rendered from
`PROVIDER_PARTICIPANT_ID|PROVIDER_CAPABILITY`.

Passing through is deliberate: it is what lets one adapter host several
capabilities. Compare all three — the payload, the `ProviderSchema` row, and
`.env` — and re-run `bin/setup.py` after changing `.env`.

While the adapter carries one configured key, onboarding a second provider is
an edit to `.env`, a re-run of `bin/setup.py` and a restart of the provider
adapter. A registry entry on its own is not enough.

**502 with an empty body.** The adapter *is* configured for the key, but the
registry has no matching `ProviderSchema` row, so no call plan resolves.
Nothing in the response says so — the reason is in the log:

```sh
docker compose logs provider-adapter | grep "no call plan"
```

Check the row exists and that its `bindingKey` matches character for
character. The registry is append-only, so a mistyped row cannot be edited —
only superseded under a new id.

**The adapters restart in a loop on the first `up`.** Expected before
`bin/setup.py` has run — there is no `config/adapters/*.yaml` yet.

**`setup.py` says the registry did not come up.** Check `docker compose ps`.
The registry waits on Keycloak, which waits on Postgres, so a cold start takes
a minute or two.

**`setup.py` says a participant is registered with a different key.** There is a
`keys/keys.json` that no longer matches the registry. Restore the old one, or
pick new `*_SUBSCRIBER_ID` values in `.env` — the old ids cannot be reused.

**The registry refuses a write with HTTP 401 and an empty body.** The token was
minted for a different issuer than the registry validates against. Check the
`X-Forwarded-Host` header is `keycloak:8080` and not the published port.

**A build fails on `go mod download`, or the adapter cannot fetch the mapping,
with "network is unreachable".** The host advertises IPv6 but cannot route it.
Add this to the adapter service in the compose file:

```yaml
    sysctls:
      - net.ipv6.conf.all.disable_ipv6=1
```

and, if the build itself is what fails, `network: host` under its `build:`.

### The gateway will not start

```sh
docker compose logs nginx-proxy-manager
docker compose exec nginx-proxy-manager nginx -t
```

`unknown limit_req zone "exp"` means the `npm-custom` mount is missing, not
that the Advanced paste is wrong — the zone is declared in
`npm-custom/http_top.conf` and has to load before any server block that
references it.

### 502 from a host that worked yesterday

Almost always a recreated adapter. NPM writes a literal `proxy_pass` hostname,
which nginx resolves at reload and then caches; a `docker compose restart` of
an adapter keeps its address, but a recreate does not.

```sh
docker compose restart nginx-proxy-manager
```

The hand-written config this replaced avoided the whole failure mode by routing
every upstream through a variable and Docker's resolver. NPM generates its own
config, so that is simply the cost of the UI.

### The certificate request fails

In order of likelihood:

- **Port 80 is not open to the world.** HTTP-01 validation arrives from Let's
  Encrypt's servers, not from you. An SG rule scoped to your IP fails here with
  a challenge timeout that reads like a DNS problem.
- **DNS does not point here yet**, or points at an address the instance lost on
  its last stop/start. Check with `dig +short <name>`, and attach an Elastic IP
  if you intend to stop the VM.
- **Rate limited.** Let's Encrypt allows 5 failed validations per hostname per
  hour. Once you hit it, fix the cause and wait — retrying is what keeps you
  there. Use their staging environment while debugging.
- **Renewal will fail the same way in sixty days** if the DNS record or the SG
  rule was only temporarily correct, and nothing will tell you at the time.

On AWS, DNS validation with the Route 53 plugin sidesteps the first two
entirely, and is the only option for a wildcard.

### A request through the gateway returns NPM's default page

The `Host` header does not match any proxy host — a missing DNS record, a
typo in the domain field, or a request made against the raw IP. NPM answers
unknown hosts itself and never consults an adapter, so this says nothing about
whether the adapter is healthy.

### 403 on /publish

Working as intended, on every host. See "What is *not* reachable" above; the
fix is not in NPM.

### 429 on the experience host

The rate limit, at 10 r/s per address with a burst of 20. A collection run that
trips it is telling you something real about the caller — but if you need
headroom for a load test, raise `rate=` in `npm-custom/http_top.conf` and
restart the gateway.

### Locked out of the admin UI

The account lives in the `npm-data` volume, and there is no reset flow. Recreate
the volume and you also lose every proxy host and certificate. Back it up:

```sh
docker run --rm -v docker-deployment_npm-data:/data -v "$PWD":/backup \
  alpine tar czf /backup/npm-data.tgz -C /data .
```

## Starting over

```sh
docker compose down -v   # -v also deletes the registry and discovery data
rm -rf keys config/adapters/exp.yaml config/adapters/network.yaml config/adapters/provider.yaml
```

Then start again from `docker compose up -d`. New keys mean new identities, so
the provider rows have to be created again too — and the old participant ids
cannot be reused.
