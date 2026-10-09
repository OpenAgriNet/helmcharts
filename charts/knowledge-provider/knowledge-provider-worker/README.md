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

No Service, no Ingress, no PersistentVolumeClaim — this chart **mounts**
volumes but never creates one. See [Shared volumes](#shared-volumes-with-knowledge-provider-api).

## Install order

Install **after** `knowledge-provider-api` (which owns the shared PVCs) and
after Temporal/MinIO/Qdrant:

```bash
helm install knowledge-provider-worker charts/knowledge-provider/knowledge-provider-worker -n knowledge-provider \
  -f charts/knowledge-provider/knowledge-provider-worker/examples/knowledge-provider-worker.dev.yaml
```

See `knowledge-provider-api`'s README for the full install order.

## Shared volumes with knowledge-provider-api

compose mounts the same `sqlite-data`, `documents-data`, `./books` and
`HF_HOME` volumes into both `api` and `worker`. This chart's
`persistence.<vol>.existingClaim` values default to the PVC names
`knowledge-provider-api` creates by default (`knowledge-provider-api-sqlite`,
`-documents`, `-books`, `-hf-cache`) — override them if you installed that
chart under a different release name. The render **fails** if an enabled
volume has no `existingClaim` set, since a worker pointed at nothing is a
silent misconfiguration otherwise.

This requires a **ReadWriteMany**-capable StorageClass (NFS, EFS, Longhorn,
CephFS, ...) in the target cluster — api and worker are separate Pods that may
be scheduled on different nodes. `books` is off by default, matching
`knowledge-provider-api`.

## Configuration

Same derivation as `knowledge-provider-api` for `TEMPORAL_HOST`,
`MINIO_ENDPOINT`, `VECTOR_DB_URL`, `PROD_VECTOR_DB_URL`, `DOCUMENT_DB_PATH`,
`ALLOWED_FILE_PATHS` — see that chart's README for the full table. This chart
has no `keycloak.*` block: auth (JWT validation, admin API calls) is handled
entirely by `knowledge-provider-api`'s HTTP layer, which this process never
touches.

`DISCOVERY_SERVICE_ENDPOINT`, `NETWORK_SENDER_ID`, `NETWORK_SENDER_URI` are
**worker-only** per `ENV.md` — the `publishing_to_network` workflow stage runs
here, not in the API.

## Secrets

Same Secret names as `knowledge-provider-api` (minus the Keycloak ones this
chart doesn't need) — point both charts at the same Secrets rather than
creating duplicates. See that chart's README, "Secrets".

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-worker charts/knowledge-provider/knowledge-provider-worker
```

Render-time guardrails:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- any enabled `persistence.<vol>.existingClaim` left blank
