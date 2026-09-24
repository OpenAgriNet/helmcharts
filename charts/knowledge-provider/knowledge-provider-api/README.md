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
| Deployment | Probes on `/health`, resources mandatory, `replicaCount` fixed at 1 (see [SQLite is single-writer](#sqlite-is-single-writer)) |
| Service | `ClusterIP` on 8001 |
| ConfigMap (env) | Non-secret config, see [Configuration](#configuration) |
| ServiceAccount | Dedicated, never `default` |
| PersistentVolumeClaim | Up to four: `sqlite`, `documents`, `books` (off by default), `hf-cache` |
| Ingress | Optional, off by default |

No Secret is rendered. Everything under [Secrets](#secrets) is referenced by
name and created outside this chart.

## Install order

```bash
# 1. Postgres for keycloak-db and temporal-db
helm install knowledge-provider-keycloak-db charts/postgresql-cnpg -n knowledge-provider -f ...
helm install knowledge-provider-temporal-db charts/postgresql-cnpg -n knowledge-provider -f ...

# 2. Auth and workflow engine
helm install knowledge-provider-keycloak charts/knowledge-provider/knowledge-provider-keycloak -n knowledge-provider -f ...
helm install knowledge-provider-temporal charts/knowledge-provider/knowledge-provider-temporal -n knowledge-provider -f ...

# 3. Object storage and DEV vector index
helm install knowledge-provider-minio charts/knowledge-provider/knowledge-provider-minio -n knowledge-provider -f ...
helm install knowledge-provider-qdrant charts/knowledge-provider/knowledge-provider-qdrant -n knowledge-provider -f ...

# 4. This chart
helm install knowledge-provider-api charts/knowledge-provider/knowledge-provider-api -n knowledge-provider \
  -f charts/knowledge-provider/knowledge-provider-api/examples/knowledge-provider-api.dev.yaml

# 5. knowledge-provider-worker, then knowledge-provider-ui
```

Starting before a dependency is up is not fatal — the `waitFor` init
containers (below) block until Temporal/MinIO/Qdrant answer, or fail the pod
after `waitFor.timeoutSeconds` (default 300s) with the dependency named.

## SQLite is single-writer

SQLite is this service's **canonical** metadata/review-state store (documents,
pages, chunks, jobs, artifacts, audit log) — not a cache. `replicaCount` must
stay `1` and autoscaling must stay off until the application migrates off
SQLite. `NOTES.txt` prints a warning if you override it upward anyway.

## Shared volumes with knowledge-provider-worker

compose mounts the same `sqlite-data`, `documents-data`, `./books` and
`HF_HOME` volumes into both `api` and `worker`. Here they are two separate
Deployments (two Pods), so sharing requires a **ReadWriteMany**-capable
StorageClass (NFS, EFS, Longhorn, CephFS, ...) — a plain `ReadWriteOnce` PVC
cannot be mounted by two Pods that may land on different nodes.

This chart is the PVC **owner**: with `persistence.<vol>.existingClaim` empty
it creates the PVC (named `<release>-sqlite`, `-documents`, `-books`,
`-hf-cache`). Point `knowledge-provider-worker`'s matching
`persistence.<vol>.existingClaim` at those exact names so both charts mount the
same underlying volume. `books` is off by default — enable it only if your
deployment relies on pre-seeded reference documents (populate the PVC once,
e.g. `kubectl cp`, after creating it) rather than user uploads. `hf-cache` is
on by default because the default `EMBEDDING_PROVIDER` is
`sentence_transformers`, which downloads and caches a local model on first use;
switch to `EMBEDDING_PROVIDER=openai_compatible` (remote embeddings) and set
`persistence.hfCache.enabled: false` to skip it.

## Configuration

Most of `ENV.md` (the app repo's full env var reference) is plain,
non-computed config and lives in `envConfig` as-is. A handful of variables are
**derived** from structured values instead, so they cannot drift apart:

| Variable | Comes from |
|---|---|
| `TEMPORAL_HOST` | `temporal.host`:`temporal.port` |
| `MINIO_ENDPOINT`, `MINIO_BUCKET` | `minio.*` |
| `VECTOR_DB_URL`, `VECTOR_DB_COLLECTION_NAME`, `VECTOR_DB_TIMEOUT_SECONDS` | `vectorStore.*` (the bundled DEV Qdrant) |
| `PROD_VECTOR_DB_URL`, `PROD_VECTOR_DB_COLLECTION_NAME`, `PROD_VECTOR_DB_TIMEOUT_SECONDS` | `prodVectorStore.*` (always external — PROD Qdrant is never bundled) |
| `DOCUMENT_DB_PATH`, `ALLOWED_FILE_PATHS` | fixed to this chart's mount paths (`/data/documents.db`, `/app/books,/data/documents`) |
| `AUTH_DISABLED` | `keycloak.authDisabled` |
| `KEYCLOAK_ISSUER`, `KEYCLOAK_JWKS_URL` | `keycloak.url` + `keycloak.realm` |
| `KEYCLOAK_AUDIENCE`, `KEYCLOAK_JWT_LEEWAY_SECONDS`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_ADMIN_*` | `keycloak.*` |

Setting `keycloak.authDisabled: false` without `keycloak.url` fails the
render — a confidential-client auth setup with no issuer to validate against
fails at boot anyway, so the render fails first and names the value.

## Secrets

None are rendered. Defaults in `secretEnv` name the Secrets this chart expects
— create them before installing, or point the values at your own:

| Secret (default name) | Keys |
|---|---|
| `knowledge-provider-minio-credentials` | `access-key`, `secret-key` — must match the `knowledge-provider-minio` release |
| `knowledge-provider-provider-keys` | `vector-db-api-key`, `prod-vector-db-api-key`, `embedding-api-key`, `hf-token`, `mistral-api-key`, `gemma-api-key`, `domain-tagging-api-key` (all optional — omit a key and that env var is simply unset) |
| `knowledge-provider-keycloak-client` | `client-secret` |
| `knowledge-provider-keycloak-admin` | `password` |
| `knowledge-provider-master-catalog-db` | `password` (BYO Postgres — see `ENV.md`) |
| `knowledge-provider-ai-layer-redis` | `password` |

`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` are wired via `secretEnv`, not
`envConfig`, even though an access key is not itself highly sensitive — it is
paired 1:1 with the secret key it authenticates, and keeping both in the same
Secret means rotating one is one edit, not two.

## Differences from compose

| | compose | chart | Why |
|---|---|---|---|
| `api`/`worker` | one `docker-compose.yml`, two services, same image | two separate Helm charts | See `knowledge-provider-worker`'s README |
| SQLite/documents/books/HF cache volumes | Docker named volumes, implicitly shared (single host) | PVCs, requires a ReadWriteMany StorageClass to share across api+worker Pods | Kubernetes Pods are not guaranteed to co-locate on one node |
| `TEMPORAL_HOST`/`MINIO_ENDPOINT`/`VECTOR_DB_URL` | hardcoded Docker-network service names in `docker-compose.yml` | derived from `temporal.*`/`minio.*`/`vectorStore.*` | Same idea, chart-parameterized service names |

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-api charts/knowledge-provider/knowledge-provider-api
```

Render-time guardrails, all of which name the value and the reason:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)
- `keycloak.authDisabled: false` with `keycloak.url` empty
