# Changelog

All notable changes to the `knowledge-provider-qdrant` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment, Service, PVC, ServiceAccount and env ConfigMap,
  wired through the `common` library chart. Single-node, matching
  `docker-compose.yml` - not a distributed Qdrant cluster, and DEV-index only
  (PROD Qdrant is always a separately managed deployment).
- Probes on Qdrant's own `/livez`/`/readyz` endpoints.
- `apiKeySecret.*` - optional, off by default to match compose's no-auth DEV
  instance; sets `QDRANT__SERVICE__API_KEY` only when a Secret name is given.
