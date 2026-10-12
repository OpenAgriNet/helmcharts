# knowledge-provider-temporal

Temporal workflow engine for the [Bharat Vistaar Docs Pipeline](https://github.com/OpenAgriNet/knowledge-provider)
— the `temporalio/auto-setup` all-in-one image, matching how
`docker-compose.yml` runs it (a single container, not Temporal's full
multi-service topology). Orchestration/retries/review-gate signaling only; no
document content is stored here — see `knowledge-provider-api`'s README on
storage responsibilities.

Internal-only: no Ingress, never exposed outside the cluster.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | `exec` probes matching compose's `tctl cluster health` healthcheck; resources mandatory |
| Service | `ClusterIP` on 7233 (gRPC frontend) |
| ConfigMap (env) | Empty by default — everything needed is derived from `database.*` |
| ServiceAccount | `automountServiceAccountToken: false` |

No Secret, no PersistentVolumeClaim — all durable state lives in Postgres.

## Install

```bash
# 1. Its database - see "The database" below for the two-database requirement
helm install knowledge-provider-temporal-db charts/postgresql-cnpg -n knowledge-provider \
  -f charts/postgresql-cnpg/examples/knowledge-provider-temporal-db.dev.yaml

# 2. Temporal
helm install knowledge-provider-temporal charts/knowledge-provider/knowledge-provider-temporal -n knowledge-provider \
  -f charts/knowledge-provider/knowledge-provider-temporal/examples/knowledge-provider-temporal.dev.yaml
```

## The database

`temporalio/auto-setup` needs **two** databases: `temporal` (workflow state)
and `temporal_visibility` (search/list APIs) — named by `database.name` and
`database.visibilityName`. `temporal` is created by CNPG's `bootstrap`;
`temporal_visibility` is **created by the entrypoint itself on first boot** —
verified against a real cluster, this is not a design choice but the only
approach that actually works, for two reasons:

1. `postgresql-cnpg`'s `databases:` list (the CNPG `Database` CRD) reuses the
   raw database name unmodified as the K8s object's `metadata.name`, which
   rejects underscores — it cannot even express `temporal_visibility` as an
   entry (the install fails outright: `"a lowercase RFC 1123 subdomain..."`).
2. Even past that, `temporalio/auto-setup` unconditionally attempts
   `CREATE DATABASE temporal_visibility` at every boot rather than checking
   for it first, so the connecting role genuinely needs `CREATEDB` — not just
   `CREATE TABLE` inside an already-provisioned database as you might expect.

Grant exactly that one extra privilege via `postInitApplicationSQL`, which
CNPG runs once as superuser when the cluster bootstraps, and let the
entrypoint create the visibility database itself:

```yaml
# knowledge-provider-temporal-db's postgresql-cnpg values - see
# charts/postgresql-cnpg/examples/knowledge-provider-temporal-db.dev.yaml
# for the full, tested example
bootstrap:
  database: temporal
  owner: temporal
  postInitApplicationSQL:
    - ALTER ROLE temporal CREATEDB;
```

## Configuration

| Variable | Comes from |
|---|---|
| `DB` | fixed to `postgres12` |
| `DB_PORT`, `POSTGRES_SEEDS`, `POSTGRES_USER`, `DBNAME`, `VISIBILITY_DBNAME` | `database.port/host/user/name/visibilityName` |
| `POSTGRES_PWD` | `database.passwordSecret` |

TLS to Postgres is not modeled here — compose runs against a plain
`postgres:15-alpine` with no TLS configured, and this chart keeps that
fidelity. If your CNPG cluster enforces TLS, this is a gap to close before
using it in that environment (the `temporalio/auto-setup` image does support
`SQL_TLS_*`-style env vars for this — not added here without verifying the
exact names against the image version in use).

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-temporal charts/knowledge-provider/knowledge-provider-temporal \
  -f charts/knowledge-provider/knowledge-provider-temporal/ci/lint-values.yaml
```

Render-time guardrails:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)
- `database.host` empty (`waitFor`, and again in the env helper)
- `database.passwordSecret.name` empty
