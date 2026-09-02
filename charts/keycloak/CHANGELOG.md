# Changelog

All notable changes to the `keycloak` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-31

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

Initial release. Configuration ported from the verified `registry/docker-compose.yml`
stack, with the deployment shape taken from Sunbird's own Helm charts
(`deploy-as-code/helm/v2`).

### Added
- Deployment, Service, env ConfigMap, realm ConfigMap, ServiceAccount, and
  optional Ingress and ESO ExternalSecret, built on the `oan-common` library.
- All ten Keycloak environment variables from the compose stack, verified at
  parity: `DB_VENDOR`, `DB_ADDR`, `DB_PORT`, `DB_DATABASE`, `DB_USER`,
  `DB_PASSWORD`, `KEYCLOAK_USER`, `KEYCLOAK_PASSWORD`, `KEYCLOAK_IMPORT` and
  `PROXY_ADDRESS_FORWARDING`.
- The `sunbird-rc` realm export shipped in `files/`, rendered into a ConfigMap
  and mounted for `KEYCLOAK_IMPORT`, so `helm install` needs nothing prepared
  beforehand. A `checksum/realm` annotation rolls the pod when it changes.
- `realmImport.existingConfigMap` to mount a realm ConfigMap managed outside the
  chart instead, and `realmImport.realmJson` to supply one inline.
- Render-time validation that `database.host`, `database.passwordSecret.name`
  and `admin.passwordSecret.name` are set, since the chart creates no passwords.
- `strategy: Recreate`, so two instances never run the realm import at once.

- Init containers that wait for the database before starting, so a first install
  does not crashloop while PostgreSQL comes up. Derived from `database.host`.
- Optional PodDisruptionBudget, which fails the render if enabled alongside a
  single replica - that combination blocks node drains entirely.
- `helm test` check that `/auth` responds and the imported realm is served.
- `extraVolumes` / `extraVolumeMounts` / `extraInitContainers` escape hatches.
- Per-environment example values for dev and production. The production example
  connects as a dedicated `keycloak` role rather than the superuser, pulls the
  admin password from ESO, and documents why it stays at one replica: the legacy
  WildFly distribution needs Infinispan cache clustering configured before a
  second replica can share sessions.

### Notes
- The shipped realm is a second copy of the compose stack's
  `registry/imports/realm-export.json`, byte-identical at the time of writing.
  Nothing enforces that: if the compose copy changes, this one needs updating and
  a chart version bump.
- Editing the realm rolls the pod but does not re-import it. The legacy image
  imports a realm only when it is absent, so changes to a live realm have to be
  made in the Keycloak console or against a fresh database.

### Changed from the compose stack
- Probes follow Sunbird's Helm chart, not the compose healthcheck: readiness and
  startup check `/auth/` on 8080, liveness is a TCP check. Compose curls the
  WildFly management port 9990, which this chart deliberately does not expose.
- Image tag defaults to `v1.0.0` rather than `latest`, matching the version
  Sunbird pairs with `sunbird-rc-core:v2.0.0`. A chart should not deploy a
  moving tag.
- Resource limits added. Sunbird's chart sets requests only; limits are
  mandatory in this repo.
