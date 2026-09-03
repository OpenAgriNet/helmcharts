# OAN stack, on Docker Compose

The whole OpenAgriNet stack for a **dev deployment on a VM**: the registry, the
discovery service, the three adapters, and a mock upstream per capability so a
request has something to answer it. One compose file, one config folder,
`make up`.

This is a dev environment. It is not production: the adapter signing keys sit
in a config file on disk, nothing terminates TLS, and every credential shipped
in `.env.example` is a public default.

## What is here, and what is not

Running here:

- **registry** — SunbirdRC, plus its Postgres and Keycloak. Holds who is on the
  network, their public keys, and which upstream API answers which capability.
  Published on the VM's loopback only, and deliberately given no route through
  the gateway — see below.
- **discovery** — catalogue search, plus its own Postgres.
- **three adapters** — experience, network and provider. Same image, three
  configs.
- **two mock upstreams** — one standing in for Mausamgram's forecast API, one
  for Agmarknet's Vistaar prices. Sources in `mocks/`; they are pulled as
  published images like everything else. They exist so the stack answers a
  select end to end out of the box, with no external API and no ngrok tunnel.
  Loopback only, and the adapter reaches them by compose service name rather
  than through the published port.
- **gateway** — Nginx Proxy Manager, the only container that publishes on a
  routable interface. Routes to the three adapters, and issues and renews the
  Let's Encrypt certificates from its own UI. Profile `gateway`.
- **hyperdx** — ClickStack: OTLP ingest, ClickHouse, and the UI over it.
  Profile `observability`.

The last two are behind profiles because neither is needed to exercise the
stack, and HyperDX is the heaviest thing here.

Deliberately **not** here:

- **a route to the registry.** It is reachable from inside the compose network
  and over an SSH tunnel to the VM, and from nowhere else. Nothing in front of
  it authenticates, and SunbirdRC uses POST for both reads and writes, so a
  route would expose creates as readily as searches. This is why `bin/setup.py`
  seeds everything: with no public registry there is no second way to write a
  row, and a Postman request could not do it.
- **a real provider API.** The mocks answer the same shapes. Pointing a
  capability at something real is a registry write — a new Participant and
  ProviderSchema row, made from inside the stack — and a base URL in `.env`.
  The provider adapter never holds that address in a config file; it reads it
  from the registry per request.

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

The provider adapter mounts two modules. The one at `/` verifies the sender's
signature against the registry; `oanProviderPublish`, on the exact path
`/publish`, has **no signature check at all**, because its intended caller is
the provider's own catalogue system inside the trust boundary. A proxy host pointed at
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

#### Worked example: discovery

`discovery` is on `oan-internal` only, so this is the two-step case — the one
where the network split does the work.

**Step 1, attach it to `oan-edge`** in `docker-compose.yml`:

```yaml
  discovery:
    networks: [oan-internal, oan-edge]
```

then `docker compose up -d discovery`. Until this, NPM cannot resolve the name
`discovery` at all and a host pointed at it fails DNS rather than working.

**Step 2, create the host.** Hosts → Proxy Hosts → Add:

| Field | Value |
|---|---|
| Domain | `discovery.oan.example.com` |
| Scheme | `http` |
| Forward Hostname | `discovery` — the compose service name, not `oan-discovery` |
| Forward Port | `8080` — the **container** port. Not `DISCOVERY_PORT`, which is only what loopback publishes it as |
| Block Common Exploits | on |

**Step 3, put an Access List on it,** because discovery answers
unauthenticated and `AUTH_ENABLE_SIGNATURE_VERIFICATION` is `false` in this
build. Nothing behind the edge will refuse a caller, so the edge is the only
authentication there is.

**Check it:**

```sh
curl -s -o /dev/null -w '%{http_code}\n' \
  https://discovery.oan.example.com/health -u user:pass    # 200

curl -s -o /dev/null -w '%{http_code}\n' \
  https://discovery.oan.example.com/health                 # 401
```

If the second returns 200 the Access List is not attached, and that failure is
silent — worth re-running after any NPM change.

#### The registry is the one you do not route

It will look like the obvious candidate: `POST /api/v1/Participant/search`
takes no token, and it is exactly the call a network peer needs. Route it
anyway and you have published more than that.

SunbirdRC uses POST for **both** reads and writes — `/Participant/search`
reads, `/Participant` creates — so no method rule tells one from the other. A
proxy host forwards the whole API. What keeps writes out today is not the
route: it is that nothing outside the VM can mint a Keycloak token, because
Keycloak publishes on `127.0.0.1`. That is a decision made elsewhere in the
compose file, and a registry route would depend on it silently. Publish
Keycloak later for an unrelated reason and the registry's write surface opens
with it, with nothing in the route changing to say so.

So `registry` is on `oan-internal` only and stays there. NPM cannot resolve
the name, which means the refusal is structural rather than a proxy host
somebody remembered not to create.

Two consequences worth knowing, because both look like bugs otherwise:

- **`bin/setup.py` has to seed everything** — all five participants and both
  capability bindings — since there is no other way to write a row. It runs on
  the VM against `127.0.0.1`.
- **the Postman collection has no registry request.** Not an omission; one
  could not work.

Reaching it to look at a row is an SSH tunnel, covered further down.

If a peer genuinely needs to read participants from outside, the answer is a
route to something that serves only that read — not a route to the registry.

#### Before you route the ones already here

Several internal services will look like obvious candidates. They are not
equivalent:

| | What routing it publishes |
|---|---|
| **discovery** | Read-mostly catalogue search. The most defensible of these, and still: it answers unauthenticated, and `AUTH_ENABLE_SIGNATURE_VERIFICATION` is `false` with nothing behind it in this build. Put an Access List on it. |
| **registry** | No — see above. A proxy host forwards reads and writes alike, and it is off `oan-edge` so one cannot be created. |
| **keycloak** | An admin console with a realm imported from a file that ships `no-user` / `no-user-password` and an admin-api client secret. Do not publish it. |
| **hyperdx** | `clickstack-local` runs single-user with **no login at all**. Publishing it hands over every trace and log the stack has collected. If it must be shared, switch to `clickstack-all-in-one` and set up a team first. |
| **the two mocks** | Pointless and confusing: they exist to be called from inside by the provider adapter, and they invent their data. Nothing outside has a reason to reach them. |
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

Nothing else. No external API and no tunnel: the two mock upstreams are part
of the stack, so a select has something to answer it the moment it comes up.

`bin/bootstrap-ubuntu.sh` installs the first two on a fresh Ubuntu VM.

## Bring it up

```sh
cp .env.example .env
make up
```

Read `.env` first. Two things in it matter before a first run:

- **the credentials.** All shipped defaults, and this file is public. Change
  them.
- `ADAPTER_IMAGE`, `DISCOVERY_IMAGE`, `MOCKIMD_IMAGE`, `MOCKAGMARKNET_IMAGE` —
  the tags published for this environment. `TAG` pins discovery on its own:
  `TAG=v0.3.1 make up` deploys a known build instead of whatever `latest`
  points at today. Nothing is built here; everything is pulled.

The rest has working defaults and is commented where the reasoning is not
obvious.

`make up` runs five steps in the order they have to happen. `make up-core`
stops after step 3, which is enough to exercise the stack:

```
1. registry and discovery        (also registry-db, keycloak, discovery-db)
2. bin/setup.py                  keys, five registry participants, adapter configs
3. mocks, then the three adapters
4. nginx-proxy-manager           the public edge -- 80 and 443, all interfaces
5. hyperdx                       ClickStack
```

Step 2 is the one to understand. It generates a keypair per adapter into
`keys/keys.json`, writes five participants and two capability bindings into
the registry, and renders the three adapter configs from the templates in
`config/adapters/`. **Nothing has to be created by hand afterwards** — and
nothing can be, from outside the VM, because the registry has no route.

Step order is not cosmetic. An adapter config is a bind-mounted *file*, and
Docker creates a *directory* at any bind-mount source that is missing — so an
adapter started before step 2 wedges on `adapter.yaml: is a directory` and
leaves a directory where step 2 needs a file. This is the entire reason the
Makefile exists rather than a line in the README saying "run these in order".
`make up` gets it right; a bare `docker compose up -d` on a fresh checkout
does not. `bin/setup.py` refuses with an explanation if it finds one of those
directories — delete them and re-run.

Re-running `make up` is safe. `setup.py` reuses the keys in `keys/keys.json`
and skips registry rows that already exist, so it converges rather than
failing on the second run.

Check it:

```sh
make ps
curl -s -X POST http://127.0.0.1:8081/api/v1/Participant/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' | python3 -m json.tool
```

Five participants: three adapters and two upstreams. That is what `setup.py`
seeded, and that curl only works on the VM itself or through a tunnel.

And through the gateway, once the proxy hosts exist:

```sh
# the routed surface
curl -s -o /dev/null -w '%{http_code}\n' \
  https://exp.oan.example.com/search            # reaches the adapter

# the two that matter more
curl -s -o /dev/null -w '%{http_code}\n' \
  https://provider.oan.example.com/publish      # 403 -- the deny is loaded
curl -s -o /dev/null -w '%{http_code}\n' \
  http://the-vm-ip/                             # NPM default page, no adapter
```

That 403 is the check worth repeating after any NPM change: it is the only
evidence that `npm-custom/server_proxy.conf` is still mounted, and losing the
mount silently opens an unauthenticated catalogue write.

## What is in the registry, and why you did not create it

`bin/setup.py` wrote all of it. Nothing in this section is a step to perform —
it is what to look at when something does not match.

**Three `node` rows, one per adapter.** These are network identities: an id, a
role, and the public halves of a keypair. The private halves stay in
`keys/keys.json` on the VM and are never in the registry. A signature between
adapters is verified against these rows.

Roles are `consumer`, `provider` and `network`, and they apply to `node` rows
only. A node needs at least one key, published as bare base64 with no encoding
label in front of it.

**Two `upstream` rows, one per mock API.** An upstream is an ordinary HTTP API
this deployment calls. It signs nothing and nothing verifies it, so it needs no
role and no keys. It holds a `baseUrl` — here a compose service name, because
these are reached from inside the network and nowhere else.

No credential for an upstream lives in the registry either. The adapter
presents credentials from its own config, which names *environment variables*
rather than values: the mandi binding uses `queryValueEnv`, and `MANDI_TOKEN`
reaches the container as an env var.

**Two `ProviderSchema` rows, one per capability.** This is the row that says
which upstream answers which capability and how to call it — method, path,
timeout, retries, and the URL of the mapping file. Its `bindingKey` is
`participantId|capabilityCode`:

```
mausamgram-mock|openagrinet:WeatherObservation
agmarknet-mock|openagrinet:MandiPrice
```

Those two strings are the hinge of the whole thing. The provider adapter builds
the same key out of each incoming payload — the provider id and the capability
`@type` it carries — and a step answers only when the key it was configured
with matches. `setup.py` renders those keys into `config/adapters/provider.yaml`
from the same `.env` values it seeds the registry from, which is what stops the
two from drifting.

### Looking at it

Only from the VM, or through a tunnel:

```sh
curl -s -X POST http://127.0.0.1:8081/api/v1/Participant/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' | python3 -m json.tool

curl -s -X POST http://127.0.0.1:8081/api/v1/ProviderSchema/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' | python3 -m json.tool
```

Search takes no token. Writes do, and the token request has a trap in it:

```sh
TOKEN=$(curl -s -X POST \
  "http://127.0.0.1:8080/auth/realms/sunbird-rc/protocol/openid-connect/token" \
  -H 'X-Forwarded-Host: keycloak:8080' -H 'X-Forwarded-Proto: http' \
  -d 'client_id=registry-frontend' -d 'grant_type=password' \
  -d 'username=no-user' -d 'password=no-user-password' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Those two `X-Forwarded-*` headers are not optional, and `keycloak:8080` is the
**container-internal** address on purpose — not whatever `KEYCLOAK_PORT`
publishes it as. Keycloak builds the token's issuer from these headers and the
registry validates that issuer against the internal address. Get it wrong and
the registry rejects the token with a 401 and an empty body.

### Pointing a capability at a real API

Two `.env` values and a re-run. To swap the weather mock for something real:

```sh
PROVIDER_PARTICIPANT_ID=imd-mausamgram      # a new id, not the mock's
MAUSAMGRAM_BASE_URL=https://the-real-api.example.gov.in
MAUSAMGRAM_PATH=/the/real/path
```

then `python3 bin/setup.py && docker compose up -d --force-recreate provider-adapter`.
That creates a new participant and a new binding, and re-renders the provider
config so its binding key matches. The old rows stay — see append-only below —
and become dead weight rather than a problem, since nothing sends their key.

Things worth knowing before editing any of this:

- **This registry is append-only.** There is no update, delete is soft, and a
  soft-deleted id keeps the unique index — so an id can never be reused. Got a
  row wrong? Pick a new id. This is why `PROVIDER_SUBSCRIBER_ID` and friends
  are worth naming deliberately the first time.
- **Change one side of a binding key only and it fails**, in one of two ways
  depending on which side. The troubleshooting section has both.
- **`path` must start with one `/` and contain no empty segment.** The schema
  refuses `//get-daily`, and so does the adapter.
- **No `{"Participant": {...}}` wrapper** on a write. The registry takes the
  record itself; a wrapper comes back as `extraneous key [Participant] is not
  permitted`.
- **Registry schemas are read at startup.** Editing anything in
  `config/registry/schemas/` needs `docker compose restart registry` before it
  takes effect.

## Test it end to end

**Quickest path: import `postman-collection/`.** Six requests, 32 assertions,
nothing to fill in — publish, discover and select for both capabilities, with
every value already matching this deployment. A green run means the stack is
healthy rather than merely answering.

The rest of this section is one of those requests as curl, if you would rather
see it than run it.

```sh
curl -s -X POST http://127.0.0.1:9202/select \
  -H 'Content-Type: application/json' \
  -d '{
    "context": {
      "version": "2.0.0", "action": "select",
      "networkId": "oan-dev",
      "transactionId": "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
      "messageId": "7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
      "timestamp": "2026-09-04T06:12:01.330Z"
    },
    "message": { "contract": { "commitments": [ {
      "status": { "descriptor": { "code": "DRAFT", "name": "Draft" } },
      "resources": [ {
        "id": "res:mausamgram:point-forecast",
        "quantity": 1,
        "resourceAttributes": {
          "@context": "https://schemas.openagrinet.global/schema/WeatherObservation/v0.1/context.jsonld",
          "@type": "openagrinet:WeatherObservation",
          "subjectCategories": ["Weather"],
          "informationMode": "OnDemand",
          "supportedObservationTypes": ["Forecast"],
          "supportedParameters": ["Rainfall", "Temperature"],
          "geographicGranularities": ["Point"],
          "location": { "type": "Point", "coordinates": [73.7898, 19.9975] }
        }
      } ],
      "offer": {
        "id": "offer:mausamgram:open-data",
        "resourceIds": ["res:mausamgram:point-forecast"],
        "provider": { "id": "mausamgram-mock",
                      "descriptor": { "code": "IMD-NWP-01", "name": "IMD Mausamgram NWP" } }
      }
    } ] } }
  }' | python3 -m json.tool
```

An `on_select` comes back with one resource per forecast day — three by
default, which is `MOCKIMD_DAYS`.

The mandi equivalent is the same call to the same endpoint with a `MandiPrice`
resource and `agmarknet-mock` as the provider, and that is the point worth
taking from this section: **one endpoint, two capabilities, and no routing
config in between.** Each provider step builds a binding key out of the
payload it is handed, answers if the key is its own, and passes the payload
through untouched if it is not. Adding a third capability is a plugin and two
registry rows, not a new route.

Two things about the payload:

**No party is named, in either direction.** Identity travels in the
`Authorization` header's `keyId`, which names the signer and the key the
registry published for it; a body that declares no caller simply skips the
declared-identity comparison. Nothing needs `bapId` or `bppId`, and the `*Uri`
fields they came with were container-internal addresses that meant nothing
outside this compose network anyway.

**The experience adapter is the only one that takes an unsigned request.** The
experience app is inside the trust boundary, so there is no network signature
to check — which is what makes this testable with a plain curl. The same call
to the provider adapter on 9200 is rejected unsigned.

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
select     you -> exp -> provider -> the upstream that owns that capability
publish    a catalogue system -> provider -> network -> discovery service
```

`discover` and `publish` both end at the discovery service, and both go
through the network adapter — that adapter is what fronts discovery, verifies
the caller and re-signs. `select` never touches it: it goes straight to the
provider adapter, which calls the upstream.

Which upstream is not in any routing table. The provider adapter runs a chain
of capability steps — weather, then mandi — and each one builds a binding key
from the payload it is handed, serves the request if the key is its own, and
passes it along untouched if not. The step that claims it looks the upstream up
in the registry by that key. So one adapter fronts both capabilities, and a
third is a plugin plus two registry rows rather than a new route or a new
port.

Each adapter's Beckn surface is mounted at the root, so a peer calling
`<baseUrl>/select` lands on the module that answers select and the `baseUrl`
the registry publishes needs no path on it. There is no prefix to strip in a
gateway rule either: a proxy host forwards to a container name and port, and
the path arrives unchanged.

**The action comes from the URL, not the payload.** The adapter strips the
module's mount path off the request path and matches what is left — `select`,
`discover` — against the routing config. The schema validator is the exception:
it reads `context.action` out of the body and ignores the path. Nothing
reconciles the two, though a mismatch usually fails validation anyway, since
two actions rarely accept the same body.

Publishing enters at the **provider** adapter, which signs and forwards:

```sh
curl -s -X POST http://127.0.0.1:9200/publish \
  -H 'Content-Type: application/json' -d @your-catalog.json
```

Three things about that:

- **It is mounted on the exact path `/publish`,** while the Beckn surface
  takes the whole subtree at `/`. Go's mux prefers the exact pattern for
  `/publish` and falls back to `/` for everything else, so `/select` still
  reaches the capability module. The two can coexist only because the patterns
  differ — give both the same path and registration panics at startup.
- **Which is why `routing-provider.yaml` keys on an empty endpoint.** Stripping
  the mount path `/publish` off the request path `/publish` leaves nothing, so
  the empty string *is* the endpoint, and there is no action left for the
  router to append to a target. Hence `excludeAction: true` and a target URL
  written out in full. It looks odd; the alternative was posting to something
  like `/internal/publish` instead, and keeping the URL the provider's
  catalogue system already uses was worth more.
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
- **`publish` is not validated here, though it could be.** The two modules
  that carry publishing — the provider adapter's root mount and the network
  adapter — declare the validator but leave `validateSchema` out of their
  `steps:`, and a plugin that is not in `steps:` never runs. That is a choice
  in this config, not a limitation: the spec does define `/catalog/publish`,
  the validator indexes it under the action `catalog/publish` that these
  payloads send, and the collection's two publish bodies validate against it
  with no errors. Turning it on is one line per module. It is off pending a
  test rather than because it cannot work.

An action the spec does not know, or a body missing a required field, comes
back as a signed NACK with a `SCH_*` code and the JSON path that failed.

## The layout

```
docker-compose.yml          the whole stack. Read it in tiers -- the banner
                            comments are the structure: registry, discovery,
                            adapters, observability (profile), edge (profile)
.env.example                copy to .env
Makefile                    the front door: make up / up-core / down / help.
bin/
  bootstrap-ubuntu.sh       docker and python on a fresh Ubuntu VM
  stack.sh                  the startup order, and why it is that order.
                            Every make target is one line of delegation here.
  setup.py                  keys, five registry rows, the adapter configs
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
    mausamgram/             one file per binding-action: the request and the
    agmarknet/              response transformation, in JSONata. These are the
                            files the adapters fetch over the raw CDN -- the
                            served copy and the reviewable copy are one file
mocks/
  mockimd/                  the two mock upstreams. Sources only: they are
  mockagmarknet/            pulled as published images like everything else.
                            See mocks/README.md for the build commands and
                            for what each deliberately gets wrong.
postman-collection/         the whole flow as a Postman collection, with the
                            deployment's own values prefilled and no registry
                            request in it
keys/keys.json              generated, gitignored. The private halves of the
                            three adapter keypairs -- the one file here that
                            is worth backing up, and the reason setup.py can
                            be re-run without invalidating what it registered
```

## About the mapping files

`config/mappings/` holds the two this deployment uses — one per binding-action
— and `MAPPING_URL` and `MANDI_MAPPING_URL` point at **this repo's own copies**
over GitHub's raw CDN. So the file a reader reviews and the file the adapter
fetches are one file, and cannot drift.

Each file has two halves. The request half turns the incoming Beckn payload
into the query string or body the upstream expects; the response half turns
what comes back into the resources that go in the answer. The mandi one is the
better example of why this is not a field-renaming exercise: it converts ISO
dates to the `dd-MM-yyyy` Agmarknet wants, sends `marketcode` only when the
request carried one, turns price strings into numbers, and omits a price that
was not reported rather than sending a zero.

It is a URL rather than a path because the registry publishes the full URL and
the adapter fetches it verbatim — which means a mapping has to be reachable
before it can be tested, and what this stack exercises is exactly what any
consumer fetches.

Note the branch in those URLs. Once this merges, point them at the default
branch, or pin a tag so a deployment is not following a moving file.

**What can be fixed here without touching code.** Quite a lot, and this is the
design intent: when a real upstream turns out to answer with different field
names, a different date format, or a nested envelope, that is a mapping edit
and a cache expiry. What is *not* fixable here is anything that depends on the
response never arriving — a non-2xx never reaches the mapping, because the
step fails first.

To change one: edit the file here and push, or publish a fork anywhere that
serves raw text over https and put that URL in the `mappings` field of the
ProviderSchema row. The adapter caches a mapping for `cacheTTL` (one minute,
in the adapter config) and GitHub's raw CDN caches for about five, so give an
edit a few minutes to show up.

## When it does not work

Both of the common failures are a binding key disagreeing with itself, and
which 404 you get says which side is wrong.

**404 `NET_ENTITY_NOT_FOUND`, "this module serves no capability matching the
request".** No provider step recognised the request as its own, so each passed
it through and nothing behind them answered.

A step decides that by building a binding key from the incoming payload — the
provider id at `message.contract.commitments[].offer.provider.id` and the
capability at `...resources[].resourceAttributes.@type` — and comparing it
against the key in its own config, which `setup.py` rendered from `.env`.

Passing through is deliberate: it is what lets this one adapter serve both
weather and mandi. Compare the payload against `.env`, and re-run
`bin/setup.py` plus `docker compose up -d --force-recreate provider-adapter`
after changing `.env`.

**404 naming a binding with no active record.** The other side. A step *is*
configured for the key, and it got as far as asking the registry which upstream
answers it — but there is no active `ProviderSchema` row with that
`bindingKey`, so no call plan resolves.

```sh
curl -s -X POST http://127.0.0.1:8081/api/v1/ProviderSchema/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' \
  | python3 -c 'import json,sys; [print(r["bindingKey"], r.get("status")) for r in json.load(sys.stdin)]'
```

Compare character for character. The registry is append-only, so a mistyped
row cannot be edited — only superseded under a new id. (This used to be a 500
with the reason only in the log; it is a 404 that names the binding now.)

**A 502 from a select, with an upstream status in it.** Not a binding problem:
the upstream itself answered non-2xx. The step reports 4xx immediately and
retries 5xx up to `retryMax` from the `ProviderSchema` row. Credentials are a
likely cause — the mandi mock answers 401 without a token, which is what
`MANDI_TOKEN` is for. The log line carries the redacted URL.

**Adding a third capability**, for reference, is a plugin in the adapter image,
one more entry under `providerSteps` *and* in `steps:` in the template, and two
registry rows. Declaring a step without adding its id to `steps:` is the quiet
failure mode: it never runs, and the request passes through to the 404 above.

**The adapters restart in a loop on the first `up`.** Expected before
`bin/setup.py` has run — there is no `config/adapters/*.yaml` yet. `make up`
sequences this correctly; a bare `docker compose up -d` does not.

**`setup.py` says the registry did not come up.** Check `make ps`. The registry
waits on Keycloak, which waits on Postgres, so a cold start takes a minute or
two — the healthcheck allows five.

**`setup.py` says a participant is registered with a different key.** There is a
`keys/keys.json` that no longer matches the registry. Restore the old one, or
pick new `*_SUBSCRIBER_ID` values in `.env` — the old ids cannot be reused.

**The registry refuses a write with HTTP 401 and an empty body.** The token was
minted for a different issuer than the registry validates against. Check the
`X-Forwarded-Host` header is `keycloak:8080` and not the published port.

**A `docker compose pull` or a mapping fetch fails with "network is
unreachable".** The host advertises IPv6 but cannot route it. Add this to the
service in question:

```yaml
    sysctls:
      - net.ipv6.conf.all.disable_ipv6=1
```

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
