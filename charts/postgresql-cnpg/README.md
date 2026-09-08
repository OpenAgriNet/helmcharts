# postgresql-cnpg

A [CloudNativePG](https://cloudnative-pg.io/)-managed PostgreSQL cluster for OAN
services. One Helm release per database.

This chart renders CNPG custom resources — a `Cluster`, and optionally an
`ObjectStore` and `ScheduledBackup` for backups. It does **not** render a
StatefulSet: the operator owns the pods, failover, and switchover. It keeps the
name `postgresql-cnpg` rather than an `oan-` prefix because it deploys
third-party software rather than an OAN-authored service.

## Prerequisites

Neither is installed by this chart, and both are cluster-wide:

1. **The CloudNativePG operator.** Without it the rendered `Cluster` is an
   unrecognised resource and nothing happens.
2. **The Barman Cloud Plugin** — only for backups. Leave `backup.enabled` false
   until its CRDs are present.

> **Status in OAN:** as of this chart's first release, neither is installed in
> `infra-automation`, and no IRSA role exists for the backup bucket. The S3
> bucket does exist (`oan-dev-pg-backup`, versioned, 30-day expiry), and its
> Terraform comment notes the IRSA role is deliberately deferred until the
> Postgres operator was chosen — this chart is that choice. Until the operator
> and role land, this chart renders and lints but cannot be usefully installed.

## Install

```bash
helm dependency update charts/postgresql-cnpg
helm install registry-db charts/postgresql-cnpg \
  -n oan-registry -f charts/postgresql-cnpg/examples/registry-db.dev.yaml
```

See [`examples/registry-db.dev.yaml`](./examples/registry-db.dev.yaml) for a
complete, commented per-environment values file.

## Databases and roles

A CNPG cluster's `bootstrap.initdb` creates **exactly one** database. Everything
else is a `Database` object the operator reconciles, so one cluster can host
several service databases without any of them sharing tables or credentials.

```yaml
# the first database, created when the cluster is bootstrapped
bootstrap:
  database: registry
  owner: registry
  ownerSecret: registry-db-app

# roles for the others, created and reconciled by the operator
managed:
  roles:
    - name: keycloak
      passwordSecret:
        name: keycloak-db      # Secret with `username` and `password`

# the others
databases:
  - name: keycloak
    owner: keycloak
```

That renders `Cluster/registry-db` plus `Database/registry-db-keycloak`, giving:

| Database | Owner | Created by |
|---|---|---|
| `registry` | `registry` | `bootstrap.initdb` |
| `keycloak` | `keycloak` | `Database` object |

**The point of the separation:** every application connects as the owner of its
own database, so no workload needs superuser rights, and one service cannot read
another's tables. `enableSuperuserAccess` stays at the CNPG default of `false`.

> **Requires CNPG >= 1.25**, where the `Database` CRD was introduced. On an older
> operator these objects are ignored *silently* — no database, and no error. If a
> database does not appear, check the operator version first.

Each entry also supports `ensure` (`present`/`absent`), `databaseReclaimPolicy`
(defaults to `retain`, so deleting the object leaves the data), `encoding`,
`locale`, `template`, `allowConnections`, `connectionLimit`, `extensions` and
`schemas`.

The render fails if you list the bootstrap database under `databases` — the
operator would be asked to create one that already exists — or if a database
names an owner with no `passwordSecret` and no `disablePassword`.

## Connecting to it

CNPG creates three services from the cluster name:

| Service | Points at |
|---|---|
| `<name>-rw` | The primary. Read-write. What applications normally use. |
| `<name>-ro` | Replicas only. Read-only. |
| `<name>-r` | Any instance, primary included. Read-only workloads that tolerate the primary. |

**Set `fullnameOverride`.** The cluster name becomes the DNS other services
depend on, and renaming a cluster later means recreating it. With
`fullnameOverride: registry-db` the primary is
`registry-db-rw.<namespace>.svc.cluster.local:5432`; without it you get
`<release>-postgresql-cnpg-rw`.

## Credentials

The owner password can come from three places:

1. **CNPG generates it** — leave `bootstrap.ownerSecret` empty. Read it from
   `Secret/<name>-app`. Fine for local and dev.
2. **A Secret you create** — set `bootstrap.ownerSecret` to its name. It must
   carry `username` and `password` keys.
3. **A Secret managed by whatever handles secrets in that environment** — point
   `bootstrap.ownerSecret` at it. This chart renders no Secrets; it only
   references them.

Superuser access is off by default, matching the CNPG default: applications
connect as the database owner, not as `postgres`.

## Backups

Base backups plus continuous WAL archiving through the Barman Cloud Plugin,
which together give point-in-time recovery. Off by default.

To enable, in this order:

1. Install the Barman Cloud Plugin cluster-wide.
2. Set `backup.objectStore.bucket` (and `path` if several clusters share the
   bucket).
3. Grant access. On EKS that is IRSA: an IAM role trusted by the cluster's
   ServiceAccount, named in `serviceAccount.annotations` as
   `eks.amazonaws.com/role-arn`. The default credentials block is
   `{inheritFromIAMRole: true}`, so no keys go anywhere near the chart.
4. Set `backup.enabled: true`.

`backup.retentionPolicy` must be **shorter** than any expiry the bucket's own
lifecycle policy applies. The OAN dev bucket expires objects after 30 days, so
the chart's `14d` default sits safely inside it. Get this backwards and S3
deletes backups Barman still believes it has.

`provider` supports `s3`, `gcs` and `azure`; `credentials` is passed through
verbatim as the matching `s3Credentials` / `googleCredentials` /
`azureCredentials` field. Because Helm merges maps, switching provider does not
remove the S3 default — the render fails and tells you to null it:

```bash
--set backup.objectStore.provider=gcs \
--set backup.objectStore.credentials.inheritFromIAMRole=null \
--set backup.objectStore.credentials.gkeEnvironment=true
```

## Storage

`storageClass` empty means the cluster's default class. On the OAN clusters that
is `gp3` with **`reclaimPolicy: Delete`**, which deletes the underlying EBS
volume when the PVC goes away. For any database you would miss, point
`storage.storageClass` and `walStorage.storageClass` at a Retain class.

`walStorage` is a separate volume for the write-ahead log, enabled by default.
It is effectively required for healthy PITR throughput.

## Migrating an existing database in

`bootstrap.import` runs CNPG's logical import (`pg_dump`/`pg_restore`) against an
existing PostgreSQL when the cluster is **first created**. It has no effect on an
existing cluster, so it is a one-shot cutover tool, not a sync.

```yaml
bootstrap:
  database: registry
  owner: registry
  import:
    enabled: true
    databases: [registry]
    source:
      host: old-postgres.example
      passwordSecret:
        name: old-postgres-superuser
```

`source.dbname` defaults to the single entry in `databases`.

## Render-time validation

The chart fails the render rather than letting a misconfiguration reach the
cluster:

| Condition | Why it matters |
|---|---|
| `resources` empty | Every OAN component must declare requests and limits (`oan-common.resources`) |
| `backup.enabled` with no `bucket` or `destinationPath` | The ObjectStore would have nowhere to write |
| `backup.objectStore.provider` not `s3`/`gcs`/`azure` | Would render an unknown credentials field |
| A credentials key not valid for the chosen provider | Catches the Helm map-merge trap described above |
| `retentionPolicy` not matching `^[1-9][0-9]*[dwm]$` | The CRD rejects it; `"14days"` fails here instead of at apply |
| `import.enabled` without `source.host` or `source.passwordSecret.name` | CNPG could not read the source |
| `import.databases` not exactly one entry | `type: microservice` permits one database |

## Configuration

See [`values.yaml`](./values.yaml) for the full commented schema. CNPG fields the
chart does not expose yet can be passed through `extraClusterSpec`, which is
merged into the `Cluster` spec.

Note on `appVersion`: for this chart it documents the PostgreSQL major line
targeted by default rather than a pullable tag, because leaving `image.repository`
empty lets the operator supply its own default image. Pin `image.*` per
environment — a PostgreSQL major upgrade is a data migration, not a tag bump.

## Known gaps

- **No restore path.** `bootstrap.recovery` (restoring a new cluster from an
  object store) is not implemented. Backups are only half a disaster-recovery
  story; this needs its own change once backups are actually running.
- **No PodMonitor by default.** `monitoring.enablePodMonitor` requires the
  Prometheus Operator CRDs, which are not installed yet.

## Versioning

Every change needs a `version` bump in `Chart.yaml` and an entry in
[`CHANGELOG.md`](./CHANGELOG.md) — see [`CONVENTIONS.md`](../../CONVENTIONS.md).
