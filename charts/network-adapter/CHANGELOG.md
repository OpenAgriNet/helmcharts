# Changelog

All notable changes to this chart are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this chart adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
