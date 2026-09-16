# postgresql-migration

Flyway schema migrations for OAN PostgreSQL databases, run as a Kubernetes Job.

It does two things, in the order you declare:

1. **Creates the per-service databases** the cluster does not create itself.
2. **Applies versioned SQL** to each database.

By default it runs as a Helm hook, so `helm install` waits for migrations to
finish before the services that depend on them start.

## Install

```bash
helm dependency update charts/postgresql-migration
helm install migrate charts/postgresql-migration \
  -n oan-registry -f charts/postgresql-migration/examples/registry-stack.dev.yaml
```

Run it **after** the database cluster and **before** Keycloak — Keycloak needs the
`keycloak` database to exist. See
[`charts/registry/README.md`](../registry/README.md) for the full stack order.

## Targets

A target is one database plus the migration directory applied to it. They run in
the order listed in `targets`:

```yaml
targets:
  - name: 01-registry     # directory under files/migrations/
    database: registry    # database to connect to (must already exist)
  - name: 02-keycloak
    database: keycloak
```

The list order is what runs; directory names are numbered only so they read in
the same order on disk. A target whose directory holds no `.sql` files is
**skipped**, and the Job logs that it skipped it — so a database can be listed
for inventory purposes without inventing migrations for it.

Set `enabled: false` on an entry to skip it without deleting it.

## This chart does not create databases

Databases are created by the [`postgresql-cnpg`](../postgresql-cnpg) chart —
`bootstrap.database` for the first, and `databases` (CNPG `Database` objects) for
the rest. The operator reconciles them.

That is deliberate. Creating them here would mean `CREATE DATABASE` outside a
transaction, no `IF NOT EXISTS` to make it idempotent, and a standing rule never
to list the database the cluster already made. The operator does it properly, and
Flyway goes back to what it is for: **migrating schemas inside databases that
already exist.**

So a target here names a database that must already exist. Pointing one at a
missing database fails with a connection error, not a helpful message.

## Adding a migration## Adding a migration

Drop a file into the target's directory, following Flyway's naming
(`V<version>__<description>.sql`):

```
charts/postgresql-migration/files/migrations/01-registry/V1__add_lookup_index.sql
```

Then bump the chart version and add a CHANGELOG entry. Two rules that Flyway
enforces and this chart cannot soften for you:

- **Never edit a migration that has already run.** Flyway stores a checksum;
  changing the file makes the next run fail validation. Add a new version
  instead.
- **Versions must not collide.** Two `V1__` files in the same target is an error.

Keycloak's own tables are the exception to all of this: it manages its schema
with Liquibase, and a second tool writing those tables would fight it. Do not add
Keycloak table DDL here.

## The connection

One host, one role, many databases — each target reuses the connection with a
different database name.

The role needs:

- **CREATEDB** for the bootstrap target.
- **Ownership or superuser rights** on each database it migrates.

In the OAN dev layout that is the CNPG superuser, whose password CNPG generates
into `Secret/<cluster>-superuser` when the database chart sets
`enableSuperuserAccess: true`.

`postgresql.host` must be the **primary** (`<cluster>-rw`). Migrations write, so
a read-only replica service fails — and fails in a way that reads like a
permissions problem.

## Secrets

The password is injected as `FLYWAY_PASSWORD` from a `secretKeyRef`. It is
**never** written into a ConfigMap — that was a real flaw in the chart this was
adapted from, where it landed in ConfigMap data in plaintext.

This chart renders no Secrets; the one it references is created by whatever
manages secrets in that environment. In the OAN dev layout CNPG generates it
(`Secret/<cluster>-superuser`).

## Running as a hook, or not

| | `hook.enabled: true` (default) | `hook.enabled: false` |
|---|---|---|
| When it runs | During `helm install`/`helm upgrade`, before other resources | As an ordinary release resource |
| Helm waits for it | Yes | No |
| `helm uninstall` removes it | No — `deletePolicy` handles cleanup | Yes |
| Repeated upgrades | Works: `before-hook-creation` deletes the old Job first | Fails if the pod template changed — Jobs are immutable |

`hook.weight: "-5"` orders this ahead of any other hook that needs the schema.
The database cluster must already be running when the hook fires, which holds in
the OAN layout because the cluster is a separate, earlier release.

## Failure handling

`repairOnFailure: true` runs `flyway repair` and retries once. Repair rewrites
the schema history to match the migrations on disk, which fixes the common case —
a previous run that failed partway and left a checksum mismatch — but it will
also happily paper over a migration someone edited. Fine for dev; consider
turning it off where you want a failed migration to stay failed until someone
looks at it.

The Job's `backoffLimit` retries the pod. That is safe: Flyway skips
already-applied migrations.

`activeDeadlineSeconds` (default 900) stops a Job that cannot reach the database
from blocking `helm install` until Helm's own timeout.

## Reading what it did

```bash
kubectl -n oan-registry logs job/migrate-postgresql-migration
```

The log names each target, the URL, the migration files it found, and whether it
skipped, succeeded or failed. Per-database history lives in
`flyway_schema_history` in each database.

Note that with the default hook `deletePolicy: before-hook-creation`, the Job and
its logs survive until the next install or upgrade replaces it.

## Configuration

See [`values.yaml`](./values.yaml) for the full commented schema and
[`examples/registry-stack.dev.yaml`](./examples/registry-stack.dev.yaml) for a
per-environment file.

## Versioning

Every change — **including adding or editing a migration** — needs a `version`
bump in `Chart.yaml` and an entry in [`CHANGELOG.md`](./CHANGELOG.md). See
[`CONVENTIONS.md`](../../CONVENTIONS.md).
