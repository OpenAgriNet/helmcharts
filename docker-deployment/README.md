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

Deliberately **not** here:

- **the provider API.** Whoever is testing runs it themselves and gives it a
  URL the VM can reach. The provider adapter never has that address in a config
  file — it reads it from the registry per request, so repointing it is a
  registry write and nothing more.
- **the provider's registry rows.** Those two are created by hand, because the
  base URL belongs to whoever runs the API. `bin/setup.py` registers only the
  three adapters.

## Reaching it

Ports bind to `127.0.0.1` on the VM by default. That is deliberate. Behind them
are a Keycloak whose admin password ships as `admin`, and a registry whose
write token anyone who has read `.env.example` can mint. On a VM's public
interface that is the whole network's identity records, writable.

So reach it over a tunnel:

```sh
ssh -L 9202:127.0.0.1:9202 -L 8081:127.0.0.1:8081 -L 8080:127.0.0.1:8080 you@the-vm
```

Then everything below works against `127.0.0.1` on your own machine.

Set `BIND_ADDR=0.0.0.0` in `.env` only once something in front is terminating
TLS and authenticating, and only after the credentials in `.env` have been
changed.

## Before you start

On the VM:

- Docker with Compose v2, logged in to wherever the images live if it is
  private — `docker login ghcr.io`
- the image tags for the adapter and the discovery service
- Python 3 and the `cryptography` package — `pip install cryptography`

And a URL the VM can reach for the upstream provider API. If that API runs on
someone's laptop, [ngrok](https://ngrok.com/) or any equivalent tunnel gives it
one.

## Bring it up

```sh
cp .env.example .env
```

Read `.env` before going on. Four things in it matter:

- `ADAPTER_IMAGE` and `DISCOVERY_IMAGE` — the tags to pull. No working default;
  set them to the tags published for this environment.
- **the credentials.** All shipped defaults. Change them.
- `BIND_ADDR` — see above.
- `PROVIDER_PARTICIPANT_ID` and `PROVIDER_CAPABILITY` have to match the registry
  rows created further down.

Then:

```sh
# 1. pulls the images, then starts registry and discovery. The adapters will
#    restart in a loop for now -- their configs do not exist yet, which step
#    2 fixes.
docker compose up -d

# 2. generate the adapter keypairs, register the three adapter identities,
#    render the three adapter configs
python3 bin/setup.py

# 3. now the adapters have configs to read
docker compose up -d
```

Check it:

```sh
docker compose ps
curl -s -X POST http://127.0.0.1:8081/api/v1/Participant/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' | python3 -m json.tool
```

Three participants, one per adapter. That is what `setup.py` seeded.

## Register the provider

Two rows. Both by hand, and both need a token.

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

**Row one — the API itself.** Type `upstream`: it has no role and no keys,
because it has never heard of Beckn.

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
      "mappings": "https://raw.githubusercontent.com/ameersohel45/oan-mappings/main/mausamgram/weather-observation.select.yaml",
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
- **An `upstream` carries no `role`, no `keys` and no credential.** It has
  never heard of Beckn, and nothing held in the registry is ever sent to it —
  the adapter presents credentials from its own config, naming environment
  variables. The schema refuses `role` or `keys` on an upstream.
- **The three roles are `consumer`, `provider` and `network`**, and they apply
  to `node` rows only — the three `setup.py` creates. A node also needs at
  least one key, published as bare base64 with no encoding label in front of
  it.
- **`bindingKey` is `participantId|capabilityCode`.** It has to match what
  `PROVIDER_PARTICIPANT_ID` and `PROVIDER_CAPABILITY` were set to in `.env`
  when `setup.py` last ran — see the troubleshooting note on bare ACKs.
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
      "bapId": "exp.oan.dev",      "bapUri": "http://exp-adapter:9202/oan",
      "bppId": "provider.oan.dev", "bppUri": "http://provider-adapter:9200/oan",
      "transactionId": "9f2c1a8e-4b70-4d31-9c55-6f2e0b1d7a44",
      "messageId": "7d41b9e0-52a6-4c18-8b73-1e9f0a4c6d22",
      "timestamp": "2026-09-02T06:12:01.330Z"
    },
    "message": { "contract": { "commitments": [ {
      "status": { "descriptor": { "code": "DRAFT", "name": "Draft" } },
      "resources": [ {
        "id": "res:point-forecast",
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

Two things about identity here:

- The **request** carries `bapId` / `bppId`, because that is the caller saying
  who it is. The adapter needs it to look the sender's key up in the registry.
- The **answer** does not echo them back. A mapping transforms a payload; it
  does not assert who anyone is, and the `*Uri` fields it could copy are
  container-internal addresses that mean nothing outside this compose network.
  Identity on the answer is the adapter's signature over it.

The experience adapter is the only one that takes an unsigned request — the
experience app is inside the trust boundary, so there is no network signature
to check. That is what makes this testable with a plain curl.

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

## The layout

```
docker-compose.yml          the whole stack
.env.example                copy to .env
bin/setup.py                keys, the three adapter rows, the adapter configs
config/
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
```

## About the mapping file

`config/mappings/` holds the same file the published mappings repository
serves. It is there to **read and to fork** — not to be served from here.

The registry row holds a full URL and the adapter fetches it verbatim, so a
mapping has to be published somewhere the adapter can reach before it can be
tested. What this stack exercises is therefore what consumers actually fetch.
Serving the local copy would prove the file works and prove nothing about the
file anyone else reads.

To change the mapping: fork it, publish the copy anywhere that serves raw text
over https, and put that URL in the `mappings` field of the ProviderSchema row.

The adapter caches a mapping for `cacheTTL` (one minute, in the adapter config)
and GitHub's raw CDN caches for about five, so give an edit a few minutes to
show up.

## When it does not work

**`{"status":"ACK"}` and no `on_select`.** The commonest one. The provider
adapter did not recognise the request as its own, so it passed it through.

It decides that by building a binding key from the incoming payload — the
provider id at `message.contract.commitments[].offer.provider.id` and the
capability at `...resources[].resourceAttributes.@type` — and comparing it
against the keys in its own config, which `setup.py` rendered from
`PROVIDER_PARTICIPANT_ID|PROVIDER_CAPABILITY`.

A mismatch is not an error, by design: passing through is what lets one adapter
host several capabilities. But nothing further answers, so the request looks
accepted and silently does nothing. Compare all three — the payload, the
`ProviderSchema` row, and `.env` — and re-run `bin/setup.py` after changing
`.env`.

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

## Starting over

```sh
docker compose down -v   # -v also deletes the registry and discovery data
rm -rf keys config/adapters/exp.yaml config/adapters/network.yaml config/adapters/provider.yaml
```

Then start again from `docker compose up -d`. New keys mean new identities, so
the provider rows have to be created again too — and the old participant ids
cannot be reused.
