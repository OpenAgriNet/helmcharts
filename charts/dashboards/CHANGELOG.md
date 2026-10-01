# Changelog

All notable changes to the `dashboards` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-01

### Added
- Initial chart. Installs the Infra, Network API and Providers dashboards as
  ConfigMaps for the Grafana chart's dashboard sidecar, in the OAN folder.
- Network API and Providers are the quick-start dashboards, unchanged; the lint
  script fails if the two copies drift.
- Infra reads kubelet metrics (`k8s.pod.*`, `k8s.volume.*`) instead of Docker
  container stats, and adds a Namespace filter.
