# Changelog

All notable changes to the `postgresql-cnpg` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-09-01

### Added
- `examples/discovery-db.dev.yaml` - the discovery service's database. It pins
  `dhi.io/pgvector:0.8-pg16`, because discovery-service's first migration
  creates the `vector` extension and no stock CloudNativePG operand image
  carries pgvector.

  Two things in it are load-bearing rather than decorative, and both were
  verified against the image rather than assumed:

  - `postInitApplicationSQL` creates `vector` and `pg_trgm`. `pg_trgm` is
    marked `trusted`, so a database owner may create it; **`vector` is not**,
    and `CREATE EXTENSION vector` as a non-superuser fails with "permission
    denied to create extension". The service connects as the bootstrap owner
    and `enableSuperuserAccess` is false, so without this the first statement
    of its migration fails and the pod crashloops on what looks like a
    credentials problem. CNPG runs this hook as superuser in the newly created
    database, which is the one moment superuser is available without granting
    it to anything long-lived.
  - `postgresUID`/`postgresGID` are 70, the image's postgres user. CNPG
    defaults to 26 and applies it as the pod's `runAsUser` and `fsGroup`.

  The image is a general-purpose PostgreSQL image, not a CNPG operand image. It
  meets CNPG's documented requirements (initdb, postgres, pg_ctl,
  pg_controldata, pg_basebackup and du on PATH; CNPG overrides its entrypoint)
  but has not been run under the operator, and it ships no barman-cloud - which
  rules out the in-core backup method, though not the Barman Cloud Plugin.

  No template changed, so this affects no existing release.

## [0.2.0] - 2026-08-31

### Added
- `databases`, rendering a CNPG `Database` object per entry, so one cluster can
  host several service databases. `bootstrap.initdb` creates only one; this covers
  the rest. Requires CNPG >= 1.25.
- `managed.roles`, rendering `spec.managed.roles` so the operator creates and
  reconciles a role per application. Together with `databases` this lets every
  service connect as the owner of its own database, with no workload holding
  superuser rights.
- Render-time validation: a database listed under `databases` that duplicates
  `bootstrap.database`, a database with no name or owner, and a managed role with
  neither `passwordSecret` nor `disablePassword`.

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

Initial release. Adapted from an existing CloudNativePG chart, with the
deployment-specific and GCP-specific parts removed and the remainder made
configurable.

### Added
- CNPG `Cluster`, plus optional Barman Cloud `ObjectStore` and
  `ScheduledBackup`, and an optional ESO `ExternalSecret` for owner credentials.
- Depends on the `oan-common` library chart for names, labels and the mandatory
  `resources` contract.
- Object store provider is selectable — `s3` (default), `gcs` or `azure` — with
  the URI scheme derived from the provider and credentials passed through
  verbatim as the matching `*Credentials` field. S3 defaults to
  `inheritFromIAMRole`, so no keys are stored anywhere.
- Render-time validation of provider, bucket/destination, credential keys,
  retention policy format, and logical-import configuration.
- `enableSuperuserAccess` (default false), `superuserSecret` and
  `primaryUpdateStrategy` exposed.
- `extraClusterSpec` escape hatch for CNPG fields not yet exposed.
- Example per-environment values file for the registry database.

### Fixed
- `nodeSelector` and `tolerations` now render inside `affinity`, where CNPG
  expects them. Previously they were emitted at the top level of the `Cluster`
  spec, which is not part of the CRD schema — the API server silently pruned
  them, so pod scheduling constraints were never applied.
- `postgresUID` and `postgresGID` are now declared in `values.yaml`. The
  templates referenced them but no schema defined them, so they could not be set
  without an undocumented override.
- `bootstrap` is omitted entirely when nothing under it is configured, instead of
  rendering a null `initdb`.
- `bootstrap.import.source.dbname` defaults to the single entry in
  `import.databases` rather than rendering an empty string.

### Changed
- `cluster.resources` moved to top-level `resources`, so the shared
  `oan-common.resources` helper and its "requests and limits are mandatory"
  guardrail apply.
- `cluster.instances`, `cluster.storage`, `cluster.walStorage`,
  `cluster.postgresql`, `cluster.affinity`, `cluster.monitoring` and
  `cluster.bootstrap` flattened to the top level; the `cluster` wrapper is gone.
- `cluster.name` replaced by the conventional `nameOverride` /
  `fullnameOverride` pair.
- Backup ServiceAccount annotations moved from `backup.serviceAccountAnnotations`
  to `serviceAccount.annotations`, since IRSA is not backup-specific.
- `ScheduledBackup`'s `immediate` and `backupOwnerReference` are configurable
  rather than hardcoded.

### Removed
- GCS-only object store, including the hardcoded `gs://` scheme,
  `gkeEnvironment: true` and the GKE Workload Identity service account
  annotation example.
- The `premium-rwo-retain` GKE storage class default; `storageClass` now defaults
  to empty (the cluster's default class).
- References to the previous deployment's bundle layout, per-bundle values files,
  Docker Hardened Images, and named example databases and users.
- Bitnami-specific framing of the logical import, which is now documented as a
  generic migration from any existing PostgreSQL, and the `TODO-CONFIRM` markers
  left in the original.
- `app.kubernetes.io/part-of: cloudnative-pg` label, replaced by the standard OAN
  labels from `oan-common`.
