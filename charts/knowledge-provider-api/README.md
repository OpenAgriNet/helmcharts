# knowledge-provider-api

The [Bharat Vistaar Docs Pipeline](https://github.com/OpenAgriNet/knowledge-provider)
API — a FastAPI service for review-driven document ingestion (OCR ->
translation -> chunking -> vector indexing), orchestrated by Temporal
workflows. Ported from the verified `knowledge-provider/docker-compose.yml`
stack.

`knowledge-provider-worker` (a separate chart) runs the same application image
as a Temporal worker instead of an HTTP server — see that chart for the split
rationale.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | Probes on `/health`, resources mandatory, runs as uid 65532 |
| Service | `ClusterIP` on 8001 |
| ConfigMap (env) | Non-secret config, see [Configuration](#configuration) |
| ServiceAccount | Dedicated, never `default` |
| PersistentVolumeClaim | `hf-cache` only (the Hugging Face model cache) |
| Ingress | Optional, off by default |

No Secret is rendered. Everything under [Secrets](#secrets) is referenced by
name and created outside this chart.

## Install order

```bash
# 1. Postgres for the app, Keycloak and Temporal
helm install knowledge-provider-db charts/postgresql-cnpg -n knowledge-provider -f ...
helm install knowledge-provider-keycloak-db charts/postgresql-cnpg -n knowledge-provider -f ...
helm install knowledge-provider-temporal-db charts/postgresql-cnpg -n knowledge-provider -f ...

# 2. Auth and workflow engine
helm install knowledge-provider-keycloak charts/knowledge-provider-keycloak -n knowledge-provider -f ...
helm install knowledge-provider-temporal charts/knowledge-provider-temporal -n knowledge-provider -f ...

# 3. Object storage and DEV vector index (shared infra, top-level charts)
helm install minio charts/minio -n knowledge-provider -f ...
helm install qdrant charts/qdrant -n knowledge-provider -f ...

# 4. This chart
helm install knowledge-provider-api charts/knowledge-provider-api -n knowledge-provider \
  -f charts/knowledge-provider-api/examples/knowledge-provider-api.dev.yaml

# 5. knowledge-provider-worker, then knowledge-provider-ui
```

Starting before a dependency is up is not fatal — the `waitFor` init
containers block until PostgreSQL/Temporal/the object store/Qdrant answer, or
fail the pod after `waitFor.timeoutSeconds` (default 300s) with the dependency
named.

## Storage

PostgreSQL is the canonical store for documents, revisions, review state,
search settings and audit. The API and the worker each apply pending SQL
migrations on startup, serialised by a PostgreSQL advisory lock. Blobs
(uploads, normalised files, stage artifacts) go to the object store, so
neither pod keeps documents on a volume.

The `knowledge-provider-db` cluster needs a database `knowledge_provider`
owned by role `knowledge_provider`; CNPG writes its password into
`knowledge-provider-db-app`, which `database.passwordSecret` points at.

Moving an existing SQLite deployment is a data migration, not a values
change: see `docs/UPGRADE.md` and the "Move data from an older stack" section
of `ENV.md` in the app repo.

The only volume left is `hf-cache`, on by default because the default
`EMBEDDING_PROVIDER` is `sentence_transformers`, which downloads a local model
on first use. Switch to `EMBEDDING_PROVIDER=openai_compatible` and set
`persistence.hfCache.enabled: false` to skip it. The worker keeps its own
cache, so the two pods need neither a ReadWriteMany volume nor one node.

## Configuration

Most of `ENV.md` (the app repo's full env var reference) is plain,
non-computed config and lives in `envConfig` as-is. A handful of variables are
**derived** from structured values instead, so they cannot drift apart:

| Variable | Comes from |
|---|---|
| `DATABASE_URL`, `PGPASSWORD` | `database.*` (the password stays out of the URL) |
| `TEMPORAL_HOST` | `temporal.host`:`temporal.port` |
| `OBJECT_STORE_*` | `objectStore.*` (`s3` for MinIO/AWS, `gcs`) |
| `VECTOR_DB_URL`, `VECTOR_DB_COLLECTION_NAME`, `VECTOR_DB_TIMEOUT_SECONDS` | `vectorStore.*` (the bundled DEV Qdrant) |
| `PROD_VECTOR_DB_URL`, `PROD_VECTOR_DB_COLLECTION_NAME`, `PROD_VECTOR_DB_TIMEOUT_SECONDS` | `prodVectorStore.*` (always external — PROD Qdrant is never bundled) |
| `AUTH_DISABLED`, `KP_RUNTIME_PROFILE` | `keycloak.authDisabled`, `runtimeProfile` |
| `KEYCLOAK_ISSUER` | `keycloak.issuerUrl` (default `keycloak.url`) + `keycloak.realm` |
| `KEYCLOAK_JWKS_URL`, `KEYCLOAK_BACKCHANNEL_URL` | `keycloak.url` + `keycloak.realm` |
| `KEYCLOAK_AUDIENCE`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CONSOLE_REDIRECT_URI`, `KEYCLOAK_GOOGLE_CONSOLE_URL`, `KEYCLOAK_PROVIDER_GROUP_ROOT`, `KEYCLOAK_VERIFY_LIVE_GROUPS`, `KEYCLOAK_ADMIN_*` | `keycloak.*` |

`keycloak.url` is the private address the API reaches Keycloak on;
`keycloak.issuerUrl` is the public one Keycloak writes into tokens, when they
differ (Keycloak behind the gateway on `/auth`).

The legacy object-store variables (`MINIO_*`, `BLOB_STORAGE_*`) still work in
the app but only when no `OBJECT_STORE_*` is set, which this chart always sets.

## Secrets

None are rendered. Defaults in `secretEnv` and `database.passwordSecret` name
the Secrets this chart expects — create them before installing, or point the
values at your own:

| Secret (default name) | Keys |
|---|---|
| `knowledge-provider-db-app` | `password` — created by CNPG for the `knowledge-provider-db` cluster |
| `knowledge-provider-minio-credentials` | `access-key`, `secret-key` — must match the `minio` release (`charts/minio`); sent as `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` |
| `knowledge-provider-qdrant` | `api-key` (optional) — same Secret as the `qdrant` release's `apiKeySecret` |
| `knowledge-provider-provider-keys` | `prod-vector-db-api-key`, `embedding-api-key`, `hf-token`, `mistral-api-key`, `gemma-api-key`, `translation-api-key`, `vllm-api-key` (all optional — omit a key and that env var is simply unset) |
| `knowledge-provider-keycloak-client` | `client-secret` (optional) |
| `knowledge-provider-keycloak-admin-client` | `client-secret` (optional) — the realm service account `keycloak.adminClientId` |

## Differences from compose

| | compose | chart | Why |
|---|---|---|---|
| `api`/`worker` | one `docker-compose.yml`, two services, same image | two separate Helm charts | See `knowledge-provider-worker`'s README |
| PostgreSQL | one shared server; `postgres-init` creates the roles and databases | one CNPG cluster per database (`knowledge-provider-db`, `-keycloak-db`, `-temporal-db`) | CNPG bootstraps the database and owner, and generates the password |
| HF cache volume | one named volume shared by api and worker | one PVC per pod | Pods are not guaranteed to land on one node |
| `TEMPORAL_HOST`/`OBJECT_STORE_ENDPOINT`/`VECTOR_DB_URL` | hardcoded Docker-network service names in `docker-compose.yml` | derived from `temporal.*`/`objectStore.*`/`vectorStore.*` | Same idea, chart-parameterized service names |

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-api charts/knowledge-provider-api
```

Render-time guardrails, all of which name the value and the reason:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)
- `keycloak.authDisabled: false` with `keycloak.url` or `keycloak.audience` empty
- `keycloak.authDisabled: true` without `runtimeProfile: local` (the app refuses to start)
- `database.host`, `database.passwordSecret.name` or `objectStore.bucket` empty
