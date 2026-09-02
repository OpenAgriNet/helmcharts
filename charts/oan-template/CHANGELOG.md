# Changelog

All notable changes to the `oan-template` chart are documented here.

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

### Added
- Initial release of the `oan-template` reference/starter application chart.
- Depends on the `oan-common` library chart via `file://../oan-common`.
- Templates for Deployment, Service, ServiceAccount, env ConfigMap, optional
  ESO ExternalSecret, and optional Ingress, all wired through `oan-common`
  helpers.
- Liveness and readiness probes and resource requests/limits enabled by default,
  per the deployment epic's requirement for every component.
- Pod-level and container-level security contexts, off by default and enabled
  per environment.
- Scheduling controls (`nodeSelector`, `tolerations`, `affinity`) and extra
  `podLabels` / `podAnnotations`.
- `envFromSecrets` for pre-existing Secrets not managed by ESO, plus `secretEnv`
  and `extraEnv` for individual environment variables.
- Fully commented `values.yaml` and a `NOTES.txt` summarising what was rendered.
