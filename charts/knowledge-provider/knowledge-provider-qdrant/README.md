# knowledge-provider-qdrant

Single-node Qdrant for the [Bharat Vistaar Docs Pipeline](https://github.com/OpenAgriNet/knowledge-provider)
— the **DEV vector index only**. Matches `docker-compose.yml`'s
single-container Qdrant, not a distributed Qdrant cluster.

**PROD Qdrant is never this chart.** Per the app's DEV/PROD dual-indexing
design (see `knowledge-provider-api`'s README), promotion to PROD always
targets a separately managed Qdrant deployment
(`knowledge-provider-api`'s `prodVectorStore.*` values) — this chart has no
"prod mode" to switch into.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | Probes on `/livez` and `/readyz`; resources mandatory |
| Service | `ClusterIP` on 6333 |
| PersistentVolumeClaim | `/qdrant/storage`, `ReadWriteOnce` — single-node, so `replicaCount` must stay 1 |
| ConfigMap (env) | Empty by default |
| ServiceAccount | `automountServiceAccountToken: false` |

No Secret is rendered by default — Qdrant has no auth unless
`apiKeySecret.name` is set.

## Install

```bash
helm install knowledge-provider-qdrant charts/knowledge-provider/knowledge-provider-qdrant -n knowledge-provider \
  -f charts/knowledge-provider/knowledge-provider-qdrant/examples/knowledge-provider-qdrant.dev.yaml
```

## Auth is optional

`apiKeySecret.name` is empty by default, matching compose's no-auth DEV
instance — `QDRANT__SERVICE__API_KEY` is only set when it is non-empty. If you
turn it on, point `knowledge-provider-api`/`-worker`'s
`secretEnv.VECTOR_DB_API_KEY` at the **same** Secret and key so the client
side stays in sync.

## Single-node, not distributed

One Qdrant process against one `ReadWriteOnce` volume. Do not raise
`replicaCount` — `NOTES.txt` warns if you do. Qdrant clustering (multiple
nodes with data distribution/replication) is a different deployment topology
than this chart models.

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-qdrant charts/knowledge-provider/knowledge-provider-qdrant
```

Render-time guardrails:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)
