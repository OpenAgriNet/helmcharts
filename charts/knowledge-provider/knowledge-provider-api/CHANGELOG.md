# Changelog

All notable changes to the `knowledge-provider-api` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment, Service, ServiceAccount, env ConfigMap, optional
  Ingress, and PersistentVolumeClaims (`sqlite`, `documents`, `books`,
  `hf-cache`), all wired through the `common` library chart.
- `waitFor` init containers blocking on Temporal, MinIO and Qdrant.
- `temporal.*`, `minio.*`, `vectorStore.*`, `prodVectorStore.*` and
  `keycloak.*` structured values, deriving `TEMPORAL_HOST`, `MINIO_ENDPOINT`,
  `VECTOR_DB_URL`, `PROD_VECTOR_DB_URL`, `KEYCLOAK_ISSUER`/`KEYCLOAK_JWKS_URL`
  so they cannot drift from the values that produce them.
- `envConfig` covering the remaining non-secret configuration from `ENV.md`
  (OCR, translation, domain tagging, chunking, rate limits, CORS, master
  catalog, AI-layer Redis, Discovery Service publish).
- `secretEnv` defaults naming the Secrets this chart expects for MinIO
  credentials, provider API keys, Keycloak client/admin credentials, and the
  Master Catalog/AI-layer Redis passwords.
- `replicaCount` fixed at 1 by convention (not autoscaled) — SQLite is this
  service's canonical, single-writer metadata store.
