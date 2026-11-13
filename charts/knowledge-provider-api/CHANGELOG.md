# Changelog

All notable changes to the `knowledge-provider-api` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-11-13

### Changed
- **Breaking:** `keycloak.realm` and `keycloak.adminRealm` default to
  `docs-pipeline` and `keycloak.clientId` to `docs-pipeline-ui`, the realm and
  client `knowledge-provider-keycloak` imports. They were `bharat-vistaar`.
- Memory defaults raised to 1Gi request / 4Gi limit. The default embedder
  (multilingual-e5-large) needs about 2.5Gi.
- `prodVectorStore.url` defaults to empty instead of a deployment's URL. It is
  needed only to promote documents to prod.
- `envConfig` sets `OTEL_SERVICE_NAME`, adds `ENABLE_PUBLISH_TO_NETWORK`
  (`"false"`) and the chandra OCR settings (`CHANDRA_INFERENCE_MODE`,
  `CHANDRA_VLLM_BASE_URL`, `VLLM_MODEL_NAME`).
- `secretEnv` adds `TRANSLATION_API_KEY` and `VLLM_API_KEY` (both optional, in
  `knowledge-provider-provider-keys`).

### Removed
- `envConfig`: `DOMAIN_TAGGING_*`, `VECTOR_BACKEND`, `PROD_VECTOR_BACKEND` and
  `NETWORK_SENDER_URI`, and `secretEnv.DOMAIN_TAGGING_API_KEY`. The app no
  longer reads them.

## [0.2.0] - 2026-09-29

### Changed
- **Breaking:** `minio.host` default changed from `knowledge-provider-minio`
  to `minio`, and `vectorStore.host` default changed from
  `knowledge-provider-qdrant` to `qdrant` — the MinIO and Qdrant charts moved
  to top-level `charts/minio`/`charts/qdrant` as shared OAN infrastructure and
  now default to release names `minio`/`qdrant` (see those charts'
  CHANGELOGs). Re-point `minio.host`/`vectorStore.host` if you keep the old
  release names.
- `examples/knowledge-provider-api.prod.yaml`: `envConfig.DOCS_PIPELINE_UI_URL`
  updated from `.../docs-pipeline` to `.../knowledge-provider`, matching the
  UI chart's ingress path fix (see `knowledge-provider-ui`'s CHANGELOG).

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
