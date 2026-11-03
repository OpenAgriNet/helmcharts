# Changelog

All notable changes to the `minio` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-29

### Changed
- **Breaking:** renamed from `knowledge-provider-minio` to `minio` and moved
  from `charts/knowledge-provider/knowledge-provider-minio` to top-level
  `charts/minio` — this is shared OAN blob-storage infrastructure, not
  knowledge-provider-specific, following the same top-level pattern as
  `postgresql-cnpg`. Chart-local helper prefix renamed to match
  (`minio.fullname` etc). Dependency path on `common` updated to
  `file://../common` (one directory shallower).
- Default release name is now `minio` instead of `knowledge-provider-minio`
  — `knowledge-provider-api`/`-worker`'s `minio.host` default was updated to
  match. Re-point `minio.host` if you keep the old release name.
- `credentialsSecret.name` default (`knowledge-provider-minio-credentials`)
  is unchanged — it names an out-of-band Secret shared with
  `knowledge-provider-api`/`-worker`, independent of the chart/release name.

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment (S3 API + console ports), Service, PVC,
  ServiceAccount and env ConfigMap, wired through the `common` library chart.
  Single-node, matching `docker-compose.yml` - not distributed MinIO.
- Bucket-init Job (`post-install,post-upgrade` hook) running `mc mb`,
  matching compose's `minio-init` service - retries until MinIO answers, then
  creates `bucket` idempotently and exits.
- `credentialsSecret.*` referencing an existing Secret for
  `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` - this chart renders no Secret.
  Defaults match `knowledge-provider-api`/`-worker`'s `MINIO_ACCESS_KEY`/
  `MINIO_SECRET_KEY` `secretEnv` so all three charts share one Secret.

### Fixed
- `image.registry`/`bucketInit.image.registry` default to `quay.io`, not
  `docker.io` - Docker Hub now rejects anonymous pulls of `minio/minio` and
  `minio/mc` entirely, even pinned tags ("pull access denied, repository does
  not exist or may require authorization"). Found by installing this chart
  against a real cluster; `quay.io/minio/minio` and `quay.io/minio/mc` are
  the currently working public sources.
