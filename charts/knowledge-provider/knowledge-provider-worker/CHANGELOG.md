# Changelog

All notable changes to the `knowledge-provider-worker` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-29

### Changed
- **Breaking:** `minio.host` default changed from `knowledge-provider-minio`
  to `minio`, and `vectorStore.host` default changed from
  `knowledge-provider-qdrant` to `qdrant` — matches `knowledge-provider-api`'s
  same change, now that the MinIO and Qdrant charts have moved to top-level
  `charts/minio`/`charts/qdrant` as shared OAN infrastructure. Re-point
  `minio.host`/`vectorStore.host` if you keep the old release names.

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment, ServiceAccount and env ConfigMap, wired through
  the `common` library chart. No Service/Ingress/probes - this workload has no
  HTTP listener.
- `command: python -m pipeline.worker` override on the same image as
  `knowledge-provider-api`.
- `waitFor` init containers blocking on Temporal, MinIO and Qdrant.
- `persistence.*.existingClaim`-only volume mounts (sqlite, documents, books,
  hf-cache), pointed at the PVCs `knowledge-provider-api` creates by default -
  this chart never creates a PVC of its own.
- `envConfig`/`secretEnv` kept at parity with `knowledge-provider-api`'s
  non-auth configuration surface.
