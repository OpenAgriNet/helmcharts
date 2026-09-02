# Changelog

All notable changes to the `registry` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-31

### Changed
- `image.repository` now defaults to empty rather than
  `sunbird-rc/sunbird-rc-core`. The image OAN will deploy is not settled, and a
  default was reading as a decision that had not been made. The render fails
  while it is empty, so this cannot be missed. The dev example sets the
  compose-verified image explicitly; the production example is a documented TODO.

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

Initial release. Configuration ported from the verified
`registry/docker-compose.yml` stack, with the deployment shape taken from
Sunbird's own Helm charts (`deploy-as-code/helm/v2`).

### Added
- Deployment, Service, env ConfigMap, schemas ConfigMap, ServiceAccount, and
  optional Ingress and ESO ExternalSecret, built on the `oan-common` library.
- All 32 registry environment variables from the compose stack, verified at
  parity. Database and Keycloak variables are derived from structured values
  rather than restated, so `OAUTH2_RESOURCES_0_URI` cannot drift from
  `keycloak.url` and `connectionInfo_uri` cannot drift from `database.*`.
- Entity schemas shipped in `files/schemas/` and rendered into a ConfigMap
  mounted read-only, with `existingConfigMap` and `inline` overrides. A
  `checksum/schemas` annotation rolls the pod when a schema changes.
- Render-time validation of `database.host`, `database.passwordSecret.name`,
  `keycloak.url`, `keycloak.adminClientSecret.name`, and
  `defaultUserPasswordSecret.name` when `keycloakUserSetPassword` is true.
  Also fails when no schema file matches, since a registry with no entity
  definitions serves nothing.
- Every optional Sunbird RC subsystem off by default, matching compose:
  encryption, events, idgen, claims, DIDs, signatures, certificates, file
  storage, notifications, async and webhooks.

- Init containers that wait for the database and for Keycloak's realm endpoint.
  The Keycloak check is HTTP against the realm rather than TCP against the port,
  because the realm is imported during Keycloak's first start and the port opens
  well before the realm exists.
- Optional HorizontalPodAutoscaler. `replicas` is omitted from the Deployment
  when it is enabled, so the two do not fight on every reconcile.
- Optional PodDisruptionBudget, which fails the render if enabled alongside a
  single replica.
- `helm test` check that `/health` responds.
- `extraVolumes` / `extraVolumeMounts` / `extraInitContainers` escape hatches.
- Per-environment example values for dev and production. The production example
  connects as the database owner rather than the superuser, pulls secrets from
  ESO, sets a PodDisruptionBudget and pod anti-affinity, and enables the pod and
  container security contexts.

### Changed from the compose stack
- Probes use `/health` (as compose does) rather than Sunbird's
  `/api/docs/swagger.json`, which would also require swagger to be enabled.
  A `startupProbe` covers first-start schema migration.
- Resource limits added; Sunbird's chart sets requests only.
- The three secret values are read from named Secret keys instead of `.env`
  variables, and the chart renders none of them.
