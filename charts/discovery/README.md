# discovery

The OAN [discover-and-publish
service](https://github.com/OpenAgriNet/discovery-service) — a Beckn v2.0.0
`/publish` and `/discover` implementation in Go. PostgreSQL is its only
datastore; there is no spatial extension and no separate search engine.

Configuration is ported from the verified `discovery-service/docker-compose.yml`
stack, with the two settings that file marks as local-only put back to their
deployment values (see [Differences from compose](#differences-from-compose)).

## What it renders

| Resource | Notes |
|---|---|
| Deployment | Probes on `/healthz` and `/readyz`, resources mandatory |
| Service | `ClusterIP` on 8080 |
| ConfigMap (env) | Overrides from `envConfig`. Empty by default — see [Configuration](#configuration) |
| ServiceAccount | `automountServiceAccountToken: false` — the service calls no Kubernetes API |
| HorizontalPodAutoscaler | Optional, off by default |
| PodDisruptionBudget | Optional, off by default |
| Ingress | Optional, off by default |
| Test Pod | `helm test` check against `/readyz` |

No Secret and no ConfigMap holding the Beckn document. Both are referenced by
name and created outside the chart.

## Install

```bash
# 1. The database — pgvector, plus `vector` created at bootstrap. Both matter; see below.
helm install discovery-db charts/postgresql-cnpg -n oan-discovery \
  -f charts/postgresql-cnpg/examples/discovery-db.dev.yaml

# 2. The service
helm install discovery charts/discovery -n oan-discovery \
  -f charts/discovery/examples/discovery.dev.yaml

helm test discovery -n oan-discovery
```

## The database

The first migration creates two extensions:

```sql
CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector: the embedding column and its HNSW index
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- contrib: trigram matching for lexical retrieval
```

No stock CloudNativePG operand image carries pgvector, so the cluster needs an
image that does. [`postgresql-cnpg`](../postgresql-cnpg) ships a ready example:

```bash
helm install discovery-db charts/postgresql-cnpg -n oan-discovery \
  -f charts/postgresql-cnpg/examples/discovery-db.dev.yaml
```

It pins `dhi.io/pgvector:0.8-pg16` — pgvector 0.8.6 and pg_trgm 1.6 on
PostgreSQL 16, amd64 and arm64. PostgreSQL **16** because that is what this
service is verified against: the design doc names it, `tests/dbtest` pins
`pgvector/pgvector:0.8.0-pg16`, and `EXPLAIN (GENERIC_PLAN)` in that suite is
PG16-and-later only. The schema does apply cleanly to 14, so this is about
staying on the tested major rather than a hard incompatibility.

Two settings in that example are load-bearing:

### `vector` is not a trusted extension

`pg_trgm` is marked `trusted = true`, so a database owner may create it.
`vector` is not:

```
ERROR:  permission denied to create extension "vector"
HINT:   Must be superuser to create this extension.
```

This service connects as the bootstrap **owner**, not the superuser, and CNPG's
`enableSuperuserAccess` is false. So without help, the very first statement of
the migration fails — and the pod crashloops on an error that reads like a bad
password rather than a missing grant.

The example creates both extensions in `bootstrap.postInitApplicationSQL`, which
CNPG runs as superuser inside the freshly created database. That is the one
moment superuser is available without granting it to anything long-lived; after
it, the service's own `IF NOT EXISTS` is a no-op and the rest of the migration
runs as the owner.

### The image's postgres user is uid 70

CNPG defaults to 26 and applies it as the pod's `runAsUser` and `fsGroup`, so
the example sets `postgresUID`/`postgresGID` to 70. Left unset, the data
directory is handed to a user that does not exist in the image.

### Not an operand image

`dhi.io/pgvector` is a general-purpose PostgreSQL image. It satisfies CNPG's
documented requirements — `initdb`, `postgres`, `pg_ctl`, `pg_controldata`,
`pg_basebackup` and `du` are all on PATH, and CNPG overrides the image's own
entrypoint — but it is not built from `cloudnative-pg/postgres-containers` and
has not been run under the operator here. **Smoke-test the first cluster to
healthy before pointing this chart at it.**

It also ships no barman-cloud, which rules out CNPG's in-core backup method.
The Barman Cloud Plugin brings its own sidecar and is unaffected.

CNPG's generated app Secret carries `username`, `password`, `dbname`, `host`,
`port`, `uri` and `jdbc-uri`. `uri` is the whole DSN, which is why
`database.urlSecret` is the recommended way to wire this chart up.

### Supplying the DSN

Exactly one of these, or the render fails:

```yaml
# Preferred — one Secret key holds the whole DSN.
database:
  urlSecret:
    name: discovery-db-app
    key: uri
```

The DSN carries host, port, database and user, so `database.host` and the rest
go unread in this form. Leave them empty rather than setting a second copy that
nothing consumes and nothing keeps true.

```yaml
# Assembled by the chart, with the password injected through Kubernetes'
# own $(VAR) expansion so it stays in the Secret.
database:
  host: discovery-db-rw
  port: 5432
  name: discovery
  user: discovery
  sslMode: prefer
  passwordSecret:
    name: discovery-db-app
    key: password
```

The assembled form renders
`postgres://discovery:$(DATABASE_PASSWORD)@discovery-db-rw:5432/discovery?sslmode=prefer`,
and the kubelet substitutes the value of the `DATABASE_PASSWORD` env var — which
comes from the Secret — at container start. The password therefore never appears
in the rendered manifest, in `helm get manifest`, or in anything that logs a
template.

The one caveat, and the reason `urlSecret` is preferred: that substitution is
textual, so a password containing `@ : / ? # %` must already be percent-encoded
in the Secret or it will silently produce a DSN that parses to the wrong thing.

### Migrations

The migrations are compiled into the binary. There is no Job, no sidecar and no
mounted directory: the service applies them on boot when `DATABASE_AUTO_MIGRATE`
is set, and treats "already at the latest version" as success.

`database.autoMigrate` is **false** by default — migrating is a step someone
decides to take, not something that happens because a pod was rescheduled.

```bash
helm upgrade discovery <chart> -n oan-discovery --reuse-values \
  --set database.autoMigrate=true --set replicaCount=1
# verify the rollout, then upgrade back with autoMigrate=false
```

Scale to one replica first. The migration runs in-process on boot, so several
pods starting at once race to apply the same one.

## The Beckn specification

The service loads the Beckn v2.0.0 document **before** it serves and refuses to
start without it. The check is unconditional: turning L1 validation off does not
remove the requirement.

It tries `becknSpec.url` first and falls back to the on-disk cache, so at least
one of `url` and `existingConfigMap` must be set. The render fails when both are
empty, because that configuration cannot boot.

| Setting | What happens |
|---|---|
| `url` only | Fetched at boot and cached into an emptyDir. Needs egress, and refetches on every reschedule. The upstream URL tracks `main`, so this pins nothing. |
| `existingConfigMap` only | Mounted read-only at the cache path. No egress needed; reproducible. The boot logs one warning about the fetch it could not do — that warning is accurate and is not a failure. |
| both | Fetch first, ConfigMap as the fallback. |

```bash
kubectl -n oan-discovery create configmap discovery-beckn-spec \
  --from-file=beckn.yaml=<discovery-service>/tests/testdata/beckn-v2.0.0.yaml
```

The document is deliberately **not shipped in this chart**, for the same reason
the Dockerfile does not bake it into the image: a copy here is a second source of
truth that ages independently of the protocol.

A volume is mounted at `becknSpec.cacheDir` either way — not only for the
air-gapped case. `/app` is not writable by uid 65532, and with
`readOnlyRootFilesystem` on, the fetch path would have nowhere to write its
cache.

## Probes

`/healthz` and `/readyz` answer different questions, and the split is
load-bearing:

| Probe | Path | Behaviour |
|---|---|---|
| startup | `/healthz` | 60 × 5s. Covers the spec fetch, and migrations when they are on |
| liveness | `/healthz` | Depends on nothing. Stays 200 with the database down |
| readiness | `/readyz` | Pings PostgreSQL. 503 when it cannot reach it |

Pointing liveness at `/readyz` would restart every pod during a database blip,
turning a recoverable outage into a crashloop across the whole Deployment. That
is why the two paths are different and why neither is configurable to the same
value by accident.

There is no `exec` probe and no compose-style healthcheck: the runtime stage is
`gcr.io/distroless/static-debian12:nonroot`, which has no shell and no curl for
one to run.

## Security context

Both security contexts are **on by default**, unlike the other OAN service
charts. The image is distroless/static running as uid 65532 with a fully static
binary: there is no shell to escalate into, and the only path the process writes
is the spec cache, which is a mounted volume. `readOnlyRootFilesystem: true`
therefore costs nothing.

If you add something that needs scratch space, add an `emptyDir` through
`extraVolumes` rather than turning this off.

## Configuration

`envConfig` is **empty by default**, and that is the deliberate part.

The image already carries `config/common.yaml` — the project's reviewed defaults
for search sizing, validation, auth and the rest — and the environment layer sits
above it. Copying those values into this chart would create a second copy of
decisions the chart did not make, and it is the second copy that rots.

Everything the chart genuinely owns is derived from structured values instead, so
`SERVER_PORT` cannot drift from the container port and `DATABASE_URL` cannot
drift from `database.*`:

| Variable | Comes from |
|---|---|
| `DATABASE_URL` | `database.urlSecret`, or assembled from `database.host/port/name/user/sslMode` + `passwordSecret` |
| `DATABASE_AUTO_MIGRATE` | `database.autoMigrate` |
| `DATABASE_MAX_CONNS`, `DATABASE_MIN_CONNS` | `database.maxConns`, `database.minConns` |
| `APP_NETWORK_ID` | `app.networkId` — **required** |
| `APP_DEFAULT_TIMEZONE` | `app.defaultTimezone` |
| `SERVER_PORT` | `service.targetPort` |
| `LOG_LEVEL` | `logLevel` |
| `VALIDATION_SPEC_URL`, `VALIDATION_SPEC_CACHE_PATH` | `becknSpec.*` |
| `EMBEDDING_PROVIDER/MODEL/DIMENSIONS/ENDPOINT` | `embeddings.*` |
| `OTEL_EXPORTER`, `OTEL_EXPORTER_OTLP_ENDPOINT` | `otel.*` |
| `REPLICATION_TARGETS` | `replicationTargets`, joined with commas |

Use `envConfig` to override a `common.yaml` default for one deployment.
Everything else the service reads:

| Variable | Default | Notes |
|---|---|---|
| `SEARCH_DEFAULT_PAGE_SIZE` | 20 | What a request naming no limit gets |
| `SEARCH_MAX_PAGE_SIZE` | 100 | A larger limit is clamped to this |
| `SEARCH_MAX_CANDIDATES_PER_MODE` | 500 | Also the reachable pagination depth |
| `SEARCH_MAX_RADIUS_METERS` | 200000 | |
| `SEARCH_READ_DEADLINE` | 2s | |
| `SEARCH_FAIL_ON_UNAVAILABLE_MODE` | false | true turns a degraded mode into a 400 |
| `RATE_LIMIT_RPS` / `RATE_LIMIT_BURST` | 20 / 40 | `burst >= rps > 0`; a bucket smaller than one second's refill can never fill |
| `SERVER_SHUTDOWN_TIMEOUT` | 15s | |
| `SERVER_MAX_REQUEST_BODY_BYTES` | 10485760 | |
| `VALIDATION_ENABLE_L1_SCHEMA` | true | |
| `VALIDATION_ENABLE_L2_CONTEXT` | false | Nothing reads it; `true` refuses the boot |
| `AUTH_ENABLE_SIGNATURE_VERIFICATION` | false | Phase 2; `true` refuses the boot |
| `EXT_ALLOW_NETWORK_FETCH` | false | A configured URL is trusted, one from a request body is not |
| `GEO_RESOLUTION_CELLS` | 8 | H3 resolution |
| `ERROR_INCLUDE_LEGACY_TYPE` | false | |

A key that matches no config field **fails the boot**, so a typo in `envConfig`
stops the service rather than being silently ignored.

`envConfig` lands in a ConfigMap. Never put a secret there — use `secretEnv` or
`envFromSecrets`.

## Differences from compose

| | compose | chart | Why |
|---|---|---|---|
| `DATABASE_AUTO_MIGRATE` | `true` | `false` | Compose says it: "left false in deployment, where migrating is a step someone decides to take" |
| `RATE_LIMIT_RPS` / `BURST` | `100000` | unset (20 / 40) | Compose says it: "effectively off for the local stack… deployments leave both unset and get the real defaults back" |
| Beckn document | bind-mounted from the working tree | fetched, or from a ConfigMap | There is no working tree to mount from |
| `LOG_LEVEL` | `debug` | `info` | `debug` in the dev example |
| PostgreSQL | `pgvector/pgvector:0.8.0-pg16` | `dhi.io/pgvector:0.8-pg16` under CNPG | Same major and same pgvector minor line, so the planner behaviour the design doc measured still holds. CNPG additionally needs `vector` created at bootstrap — see [The database](#the-database) |
| healthcheck | none — distroless has no shell | HTTP probes | The compose file notes the probes are HTTP and "for you rather than for Compose" |
| Superuser | `POSTGRES_USER=discovery` **is** the superuser | the service owns its own database and is not superuser | This is why the `vector` extension problem appears only in the cluster: in compose the migration creates it as superuser without anyone noticing it needed to be one. The cluster creates it at bootstrap instead |

## Validation

```bash
../../scripts/lint-charts.sh
```

Two `ci/*-values.yaml` files render as separate cases, deliberately covering
opposite branches: `lint-values.yaml` is the assembled DSN with a fetched spec,
`url-secret-values.yaml` is the whole-DSN Secret with a ConfigMap spec, plus
autoscaling-adjacent extras, Ingress and a PDB. A chart that only ever renders
its defaults has untested branches that fail on the day someone uses them.

Render-time guardrails, all of which name the value and the reason:

- `image.repository` empty
- `app.networkId` empty
- neither `database.urlSecret.name` nor `database.passwordSecret.name`
- `database.host` empty while the DSN is assembled
- neither `becknSpec.url` nor `becknSpec.existingConfigMap`
- `embeddings.provider` other than `noop` with no `embeddings.endpoint`
- `otel.exporter` other than `none` with no `otel.endpoint`
- `resources` empty (`oan-common`)
- a probe with no handler or with two (`oan-common`)
- a PodDisruptionBudget alongside a single replica
