# minio

Single-node MinIO — shared OAN blob-storage infrastructure, top-level
alongside `postgresql-cnpg` since more than one service may need it. Today's
only consumer is the [knowledge-provider](https://github.com/OpenAgriNet/knowledge-provider)
docs pipeline, for original uploads, normalized files, and stage artifacts.
Matches `docker-compose.yml`'s single-container
`minio server /data --console-address :9001`, **not** a distributed/HA MinIO
deployment.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | Single container, ports `http` (S3 API, 9000) and `console` (9001); probes on `/minio/health/{live,ready}`; resources mandatory |
| Service | `ClusterIP`, both ports |
| PersistentVolumeClaim | `/data`, `ReadWriteOnce` — single-node, so `replicaCount` must stay 1 |
| Job (`*-bucket-init`) | `post-install,post-upgrade` hook running `mc mb`, matching compose's `minio-init` service. Retries until MinIO answers, then creates the bucket idempotently and exits |
| ConfigMap (env) | Empty by default |
| ServiceAccount | `automountServiceAccountToken: false` |

No Secret is rendered — see [Secrets](#secrets).

## Install

```bash
kubectl -n knowledge-provider create secret generic knowledge-provider-minio-credentials \
  --from-literal=access-key=<access-key> --from-literal=secret-key=<secret-key>

helm install minio charts/minio -n knowledge-provider \
  -f charts/minio/examples/minio.dev.yaml
```

Release name matters: `knowledge-provider-api`/`-worker`'s `minio.host`
defaults to `minio`, assuming this chart is installed under that release name
in the same namespace — override `minio.host` on those charts if you name
this release differently.

The bucket-init Job runs automatically on install/upgrade — check it with
`kubectl logs job/minio-bucket-init` if `knowledge-provider-api` reports the
bucket missing.

## Secrets

None are rendered. `credentialsSecret.name` (default
`knowledge-provider-minio-credentials`, kept as-is across the chart's move to
this top-level location) names the Secret both the main container and the
bucket-init Job read `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` from — keys
`access-key`/`secret-key` by default (`credentialsSecret.*Key`).

Use the **same** Secret name/keys on `knowledge-provider-api` and
`knowledge-provider-worker`'s `secretEnv.MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`
(their defaults already match this chart's default) — one Secret, three
charts, not three copies of the same credential.

## Single-node, not distributed

This is one MinIO process against one `ReadWriteOnce` volume, exactly like
compose. Do not raise `replicaCount` — a second replica would either fail to
mount the same `ReadWriteOnce` PVC or, if it somehow did, corrupt MinIO's
on-disk state, since single-node MinIO is not designed for concurrent writers
against the same volume. `NOTES.txt` warns if you override it anyway. Moving
to distributed MinIO (multiple drives/nodes) is a different deployment
topology, not a `replicaCount` bump.

## Validation

```bash
../../scripts/lint-charts.sh
helm template minio charts/minio
```

Render-time guardrails:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)
- `credentialsSecret.name` empty
