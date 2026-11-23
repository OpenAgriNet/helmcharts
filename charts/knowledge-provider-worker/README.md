# knowledge-provider-worker

The [Bharat Vistaar Docs Pipeline](https://github.com/OpenAgriNet/knowledge-provider)
Temporal worker — runs the **same application image** as
[`knowledge-provider-api`](../knowledge-provider-api), with
`command: python -m pipeline.worker` instead of `uvicorn`, registering
workflows/activities on the `ocr-pipeline` task queue. No HTTP server, no
Service, no Ingress, no probes.

## Why a separate chart from knowledge-provider-api

compose runs `api` and `worker` as two services from one image; here they are
two Helm charts rather than one chart with a role switch, so each has its own
independent release lifecycle, rollout, and values file — at the cost of
duplicating the (large) shared config surface between the two charts'
`values.yaml`. Keep both in sync when the app's env var contract changes.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | No ports, no probes (compose defines none for `worker` either); resources still mandatory |
| ConfigMap (env) | Non-secret config, kept at parity with `knowledge-provider-api`'s `envConfig` |
| ServiceAccount | Dedicated, never `default` |
| PersistentVolumeClaim | `hf-cache`, this pod's own Hugging Face model cache |

No Service and no Ingress.

## Install order

Install alongside `knowledge-provider-api`, after PostgreSQL, Temporal, the
object store and Qdrant:

```bash
helm install knowledge-provider-worker charts/knowledge-provider-worker -n knowledge-provider \
  -f charts/knowledge-provider-worker/examples/knowledge-provider-worker.dev.yaml
```

See `knowledge-provider-api`'s README for the full install order.

## Storage

The worker shares state with the API through PostgreSQL, the object store and
Qdrant, not through volumes, so it can run on any node. It applies pending SQL
migrations on startup too, under the same advisory lock as the API. Its only
volume is its own `hf-cache` PVC (`<release>-hf-cache`, or
`persistence.hfCache.existingClaim`).

## Configuration

Same derivation as `knowledge-provider-api` for `DATABASE_URL`/`PGPASSWORD`,
`TEMPORAL_HOST`, `OBJECT_STORE_*`, `VECTOR_DB_*` and `PROD_VECTOR_DB_*` — see
that chart's README for the full table. Keep `database`, `objectStore` and
`vectorStore` identical in both charts. This chart has no `keycloak.*` block:
auth is handled entirely by `knowledge-provider-api`'s HTTP layer.

`ENABLE_PUBLISH_TO_NETWORK`, `DISCOVERY_SERVICE_ENDPOINT` and `NETWORK_SENDER_ID` are
**worker-only** per `ENV.md` — the `publishing_to_network` workflow stage runs
here, not in the API.

## Secrets

Same Secret names as `knowledge-provider-api` (minus the Keycloak ones this
chart doesn't need) — point both charts at the same Secrets rather than
creating duplicates. See that chart's README, "Secrets".

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-worker charts/knowledge-provider-worker
```

Render-time guardrails:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- `database.host`, `database.passwordSecret.name` or `objectStore.bucket` empty
