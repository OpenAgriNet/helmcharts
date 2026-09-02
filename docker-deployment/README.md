# OAN stack, on Docker Compose

The whole OpenAgriNet stack on one machine: the registry, the discovery
service, and the three adapters. One compose file, one config folder.

This is for trying the network end to end on your own laptop. It is not a
production deployment — the keys live in a config file and nothing runs TLS.

## What is here, and what is not

Running here:

- **registry** — SunbirdRC, plus its Postgres and Keycloak. Holds who is on
  the network, their public keys, and which upstream API answers which
  capability.
- **discovery** — catalogue search, plus its own Postgres.
- **three adapters** — experience, network and provider. Same image, three
  configs.

Deliberately **not** here:

- **your provider API.** You run it yourself and expose it with ngrok. The
  provider adapter never has its address in a config file — it reads it from
  the registry at request time, so swapping tunnels is a registry edit.
- **the provider's registry rows.** You create those two by hand, because the
  base URL is your tunnel. `bin/setup.py` registers only the three adapters.

## Before you start

- Docker with Compose v2
- Python 3 and the `cryptography` package — `pip install cryptography`
- [ngrok](https://ngrok.com/) or any other way to give your local API a public
  https URL

## Bring it up

```sh
cp .env.example .env
```

Read `.env` before going on. Two things in it matter:

- `ADAPTER_SRC` points at a **feature branch**, because the three OAN plugins
  are not on the adapter's default branch yet.
- `PROVIDER_PARTICIPANT_ID` and `PROVIDER_CAPABILITY` have to match the
  registry rows you create further down.

Then:

```sh
# 1. registry and discovery. The adapters will fail to start for now --
#    their configs do not exist yet, which step 2 fixes.
docker compose up -d

# 2. generate the adapter keypairs, register the three adapter identities,
#    render the three adapter configs
python3 bin/setup.py

# 3. now the adapters have configs to read
docker compose up -d
```

The first run builds the adapter and discovery images from source, so give it
a few minutes.

Check it:

```sh
docker compose ps
curl -s -X POST http://localhost:8081/api/v1/Participant/search \
  -H 'Content-Type: application/json' -d '{"filters":{}}' | python3 -m json.tool
```

Three participants, one per adapter. That is what `setup.py` seeded.

## Register your provider

Two rows. Both by hand, and both need a token.

Start your API and expose it:

```sh
ngrok http 9100
```

Take the `https://` URL ngrok prints. Get a token:

```sh
TOKEN=$(curl -s -X POST \
  "http://localhost:8080/auth/realms/sunbird-rc/protocol/openid-connect/token" \
  -H 'X-Forwarded-Host: keycloak:8080' -H 'X-Forwarded-Proto: http' \
  -d 'client_id=registry-frontend' -d 'grant_type=password' \
  -d 'username=no-user' -d 'password=no-user-password' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
```

Those two `X-Forwarded-*` headers are not optional. Keycloak runs behind
`PROXY_ADDRESS_FORWARDING` here, and without them it answers with an empty
body.

**Row one — the API itself.** Type `upstream`: it has no role and no keys,
because it has never heard of Beckn.

```sh
curl -s -X POST http://localhost:8081/api/v1/Participant \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $TOKEN" \
  -d '{
    "participantId": "my-weather-api",
    "name": "My weather API",
    "type": "upstream",
    "status": "active",
    "baseUrl": "https://YOUR-NGROK-SUBDOMAIN.ngrok-free.app",
    "auth": { "scheme": "none" }
  }'
```

**Row two — which capability it answers, and how to call it.**

```sh
curl -s -X POST http://localhost:8081/api/v1/ProviderSchema \
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
- **`bindingKey` is `participantId|capabilityCode`.** It has to match what
  `PROVIDER_PARTICIPANT_ID` and `PROVIDER_CAPABILITY` were set to in `.env`
  when you ran `setup.py`, because that is the key the provider adapter was
  configured to answer to.
- **`path` must start with one `/` and contain no empty segment.** The schema
  refuses `//get-daily`, and so does the adapter.
- **This registry is append-only.** There is no update, delete is soft, and a
  soft-deleted id keeps the unique index — so an id can never be reused. Got a
  row wrong? Pick a new id.

## Test it end to end

Replace `my-weather-api` if you used a different id, and point the coordinates
at wherever your API has data.

```sh
curl -s -X POST http://localhost:9202/beckn/select \
  -H 'Content-Type: application/json' \
  -d '{
    "context": {
      "version": "2.0.0", "action": "select",
      "networkId": "local-network",
      "bapId": "exp.oan.local",      "bapUri": "http://exp-adapter:9202/beckn",
      "bppId": "provider.oan.local", "bppUri": "http://provider-adapter:9200/beckn",
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

The experience adapter is the only one that takes an unsigned request — the
experience app is inside the trust boundary, so there is no network signature
to check. That is what makes this testable with a plain curl.

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
    routing-exp.yaml        which action goes to which adapter
    routing-network.yaml
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

To change the mapping: fork it, publish your copy anywhere that serves raw
text over https, and put that URL in the `mappings` field of your
ProviderSchema row.

The adapter caches a mapping for `cacheTTL` (one minute, in the adapter
config) and GitHub's raw CDN caches for about five, so give an edit a few
minutes to show up.

## When it does not work

**`{"status":"ACK"}` and no `on_select`.** The provider adapter did not
recognise the request as its own, so it passed it through. The binding key in
your `ProviderSchema` row does not match what the adapter is configured for.
Compare the row against `PROVIDER_PARTICIPANT_ID` and `PROVIDER_CAPABILITY` in
`.env`, and re-run `bin/setup.py` if you change them.

**The adapters restart in a loop on the first `up`.** Expected before
`bin/setup.py` has run — there is no `config/adapters/*.yaml` yet.

**`setup.py` says the registry did not come up.** Check `docker compose ps`.
The registry waits on Keycloak, which waits on Postgres, so a cold start takes
a minute or two.

**`setup.py` says a participant is registered with a different key.** You have
a `keys/keys.json` that no longer matches the registry. Restore the old one, or
pick new `*_SUBSCRIBER_ID` values in `.env` — the old ids cannot be reused.

**A build fails on `go mod download`, or the adapter cannot fetch the mapping,
with "network is unreachable".** Your machine advertises IPv6 but cannot route
it. Add this to the adapter service in the compose file:

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
the provider rows have to be created again too.
