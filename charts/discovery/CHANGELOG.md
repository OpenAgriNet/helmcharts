# Changelog

All notable changes to the `discovery` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-01

Initial release. Configuration ported from the verified
`discovery-service/docker-compose.yml` stack, built on the `oan-common` library.

### Added
- Deployment, Service, env ConfigMap, ServiceAccount, and optional Ingress,
  HorizontalPodAutoscaler and PodDisruptionBudget.
- Two ways to supply `DATABASE_URL`, exactly one of which must be set:
  `database.urlSecret` for the whole DSN from one Secret key — which is what
  CloudNativePG's generated `<cluster>-app` Secret already provides under `uri`
  — or `database.host/port/name/user/sslMode` plus `passwordSecret`, from which
  the chart assembles a DSN with the password injected through Kubernetes'
  `$(VAR)` expansion so it stays in the Secret and out of the rendered manifest.
- Every other environment variable derived from structured values rather than
  restated, so `SERVER_PORT` cannot drift from the container port and
  `DATABASE_URL` cannot drift from `database.*`.
- Beckn specification wiring: `becknSpec.url` for the fetch path,
  `becknSpec.existingConfigMap` for the air-gapped one, and a volume mounted at
  the cache path either way — `/app` is not writable by uid 65532, so with
  `readOnlyRootFilesystem` on the fetch would have nowhere to cache.
- Split probes: liveness and startup on `/healthz`, readiness on `/readyz`.
  Liveness on `/readyz` would restart every pod during a database blip, turning
  a recoverable outage into a crashloop across the Deployment.
- Render-time validation of `app.networkId`, the database DSN, the Beckn spec
  source, and the endpoint that a non-default `embeddings.provider` or
  `otel.exporter` requires. Each names the value and why the boot needs it.
- `helm test` check against `/readyz` rather than `/healthz`: `/healthz` is 200
  with the database down and would pass on a deployment that cannot serve a
  single discover.
- `extraVolumes` / `extraVolumeMounts` / `extraInitContainers` escape hatches.
- No init container waiting on PostgreSQL, unlike the `registry` chart. The
  service fails fast when it cannot connect and Kubernetes' restart backoff is
  already the retry, so a wait container buys a tidier first install at the cost
  of a second thing to configure and keep pointed at the right host.
- Per-environment example values for dev and production. Production mounts the
  Beckn document from a ConfigMap rather than fetching it, sets a
  PodDisruptionBudget and pod anti-affinity, and leaves `image.repository` as a
  documented TODO.

### Changed from the compose stack
- `DATABASE_AUTO_MIGRATE` defaults to `false`. The compose file states the
  intent directly: migrating is a step someone decides to take. The README
  documents the scale-to-one-replica upgrade that does it.
- The rate limiter is left unset, so the service's own 20 rps / 40 burst
  defaults apply. Compose's 100000/100000 is a local-stack ceiling, not a
  deployment value.
- `envConfig` is empty. The image already carries the reviewed
  `config/common.yaml` defaults and the environment layer sits above it;
  restating them here would be a second copy of decisions this chart did not
  make.
- Both security contexts are on by default, unlike the other OAN service
  charts. The image is distroless/static as uid 65532, so
  `readOnlyRootFilesystem` costs nothing once the spec cache is a volume.
- `automountServiceAccountToken` is false: the service calls no Kubernetes API.

### Known prerequisite
- PostgreSQL must have **pgvector**, and must create the `vector` extension at
  bootstrap. `postgresql-cnpg` 0.2.1 ships
  `examples/discovery-db.dev.yaml`, which does both: it pins
  `dhi.io/pgvector:0.8-pg16` and creates the extension in
  `postInitApplicationSQL`.

  The bootstrap half is not optional. `vector` is not a trusted extension, so
  the database owner this service connects as cannot create it - only a
  superuser can, and `enableSuperuserAccess` is false. Without it the first
  statement of the migration fails and the pod crashloops on an error that
  reads like a credentials problem. `pg_trgm` is trusted and would have
  succeeded, which is what makes the failure look selective and confusing.

  Neither condition is detectable at render time; the README describes both
  failure modes.
- No image is published for `discovery-service` — its CI builds and scans one
  but pushes nothing — so `image.repository` is empty and the render fails until
  an environment supplies a tag.
