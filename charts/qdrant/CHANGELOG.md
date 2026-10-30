# Changelog

All notable changes to the `qdrant` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-29

### Changed
- **Breaking:** renamed from `knowledge-provider-qdrant` to `qdrant` and moved
  from `charts/knowledge-provider/knowledge-provider-qdrant` to top-level
  `charts/qdrant` — this is shared OAN vector-index infrastructure, not
  knowledge-provider-specific, following the same top-level pattern as
  `postgresql-cnpg`. Chart-local helper prefix renamed to match
  (`qdrant.fullname` etc). Dependency path on `common` updated to
  `file://../common` (one directory shallower).
- Default release name is now `qdrant` instead of `knowledge-provider-qdrant`
  — `knowledge-provider-api`/`-worker`'s `vectorStore.host` default was
  updated to match. Re-point `vectorStore.host` if you keep the old release
  name.

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment, Service, PVC, ServiceAccount and env ConfigMap,
  wired through the `common` library chart. Single-node, matching
  `docker-compose.yml` - not a distributed Qdrant cluster, and DEV-index only
  (PROD Qdrant is always a separately managed deployment).
- Probes on Qdrant's own `/livez`/`/readyz` endpoints.
- `apiKeySecret.*` - optional, off by default to match compose's no-auth DEV
  instance; sets `QDRANT__SERVICE__API_KEY` only when a Secret name is given.
