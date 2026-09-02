# Changelog

All notable changes to the `postgresql-migration` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-31

### Removed
- The `00-bootstrap` target and its `CREATE DATABASE` migration. Databases are now
  created by the `postgresql-cnpg` chart, via `bootstrap.database` and the CNPG
  `Database` CRD, which the operator reconciles properly. Doing it in Flyway meant
  `CREATE DATABASE` outside a transaction (with a `.sql.conf` sidecar), no
  `IF NOT EXISTS`, and a standing rule never to list the database the cluster
  itself creates. Leaving both in place would have failed outright, since the
  operator creates the database first.

  This chart now only migrates schemas inside existing databases, which is what
  Flyway is for.

### Removed
- External Secrets Operator integration: the `ExternalSecret` template and the
  `externalSecrets` value block. ESO is not installed in any OAN cluster, so this
  was configuration that could not be exercised, and a chart that renders a
  Secret-producing resource invites the question of where secrets come from to be
  answered differently per chart.

  Charts still reference Secrets by name - `envFromSecrets`, `secretEnv`, and the
  per-chart `*Secret.name` settings are unchanged. Creating those Secrets is now
  unambiguously outside the charts.

## [0.1.0] - 2026-08-31

Initial release. Adapted from an existing Flyway migration chart, rebuilt on
`oan-common` with the previous deployment's specifics removed.

### Added
- Flyway migration Job, env ConfigMap, generated-script ConfigMap, one ConfigMap
  per target, ServiceAccount, and an optional ESO ExternalSecret.
- Multi-target migrations: each entry in `targets` is one database plus its
  migration directory, applied in the declared order. `enabled: false` skips an
  entry without removing it.
- A bootstrap migration that creates the `keycloak` database, shipping the
  `.sql.conf` sidecar with `executeInTransaction=false` that PostgreSQL requires
  for `CREATE DATABASE`.
- Runs as a Helm hook (`pre-install`, `pre-upgrade`) by default, so migrations
  complete before dependent services start. `hook.enabled: false` installs it as
  an ordinary release resource instead.
- `repairOnFailure` (default true) runs `flyway repair` and retries once.
- Render-time validation of `postgresql.host` and
  `postgresql.passwordSecret.name`, and of every target: name and database
  present, no duplicate names, and a matching directory in the chart.
- Targets with no `.sql` files are skipped at runtime with a log line, so a
  database whose schema is managed elsewhere (Keycloak's Liquibase, Sunbird RC's
  own DDL) can still be listed for inventory.
- `activeDeadlineSeconds` (default 900), so an unreachable database fails the Job
  rather than blocking `helm install` until Helm's timeout.

### Fixed
- **The database password is no longer written to a ConfigMap.** The original
  chart rendered `FLYWAY_PASSWORD` and `PGPASSWORD` into ConfigMap data via its
  `configs/env.yaml`, which put a live credential in plaintext in an object that
  is not treated as sensitive. It is now injected from a `secretKeyRef`, and the
  chart renders no passwords at all.
- Volume mounts for migration directories are now inside the block that emits
  the `volumeMounts:` key. Previously the per-folder `range` sat outside the
  `if configmap.enabled` guard, so disabling the ConfigMap while keeping
  migration folders emitted orphaned list items with no parent key — invalid
  YAML. The same applied to `volumes:`.
- `FLYWAY_LOCATIONS` no longer disagrees with the actual mount path. The original
  set it to `filesystem:/flyway/migrations` while the script passed
  `-locations=filesystem:/migrations/$folder` explicitly and the volumes mounted
  at `/migrations/<folder>`, so the environment variable was misleading dead
  configuration.
- Target ordering is explicit in `targets` rather than derived from
  `ls /migrations | sort -n`, which silently depended on directory names carrying
  numeric prefixes.

### Changed
- Built on `oan-common`, replacing the external `common` library chart pulled
  from a third-party Helm repository.
- The migration script is generated from `targets` rather than shipped as a
  static file that parsed a directory listing, so each target gets its own JDBC
  URL and one Job can migrate several databases.
- Script is POSIX `sh`, not `bash`, so it runs on the Alpine-based Flyway image.
- Image tag pinned (`11.10.0-alpine`) rather than defaulting to `latest`.
- Resource requests and limits are now mandatory, via `oan-common.resources`.

### Removed
- `Service`, `Ingress`, `autoscaling`/HPA, `serviceMonitor`, and liveness and
  readiness probes. This chart renders a Job; none of them apply to one.
- `replicaCount`, which a Job does not have.
- Configuration belonging to the previous deployment and unrelated to database
  migration: `superset_oauth_clientid`, `superset_oauth_client_secret`,
  `kong_ingress_domain`, `gf_auth_generic_oauth_client_id`,
  `gf_auth_generic_oauth_client_secret`, `web_console_user`,
  `web_console_password`, `web_console_login`, and the `system_settings` block
  (`encryption_key`, `default_dataset_id`, `max_event_size`, `dedup_period`).
- The `global.postgresql.password` value, along with `global.*` indirection and
  the `base.namespace` override mechanism that let a parent chart retarget the
  namespace per subchart.
- The discovery-specific migrations (`02-discover-db`), which belong to a
  different service and a different database.
