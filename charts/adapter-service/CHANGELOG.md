# Changelog

All notable changes to this chart are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this chart adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Renamed from `network-adapter` to `adapter-service`, and generalised from one
  adapter to all three (#2). provider, network and experience run the same image and
  the same config format, so `role` now selects what differed between them
  rather than each needing its own chart.
- `role` is required and validated against `provider|network|experience`. It has no
  default: a default would hand one adapter another one's handler role and step
  list, which renders, starts, reports Ready and then mis-handles every request.
- `handler.role` and `handler.steps` derive from `role` and are overridable.
  `experience` gets `bap` and `[addRoute, sign]` — it sits inside the trust boundary
  and takes unsigned requests, so it has no signature to validate. The other
  two get `bpp` and `[validateSign, addRoute, sign]`.
- `discovery.url` replaced by `routing.rules`, a list. `provider` fans out to
  several upstreams; a single target could not express that.
- Config placeholders renamed `__NETWORK_*` to `__ADAPTER_*`, and the routing
  file to `routing-<role>.yaml`.
- `appName` and `otel.serviceName` default to `<role>-adapter` and
  `oan-<role>-adapter`, matching the compose stack's service and OTEL names.
- `http.timeout` is now a value rather than hardcoded.

### Added

- `examples/{provider,network,experience}.yaml` — one values file per role.
- `ci/otel-ingress-values.yaml` now renders the `experience` role, so the role
  branches that `ci/lint-values.yaml` (network) does not reach are covered.
- NOTES print the role, resolved step list and routing targets, and warn that
  an Ingress on the `experience` role exposes an unauthenticated entry point.

## [0.1.0] - 2026-09-04

### Added

- Initial chart, modelled on the `network-adapter` service in
  `docker-deployment/docker-compose.yml` (#2).
- Config rendered from a ConfigMap of placeholders plus an identity Secret,
  substituted by an init container into an `emptyDir` — so the keypair is
  never written to a ConfigMap and never appears in a process environment.
- Render-time failures for the five values whose absence would otherwise
  produce a pod that runs, reports Ready, and does not work.
- Optional HPA, PodDisruptionBudget, Ingress and upstream readiness gates, all
  off by default.
