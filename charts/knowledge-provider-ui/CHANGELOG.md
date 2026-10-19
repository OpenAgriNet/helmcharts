# Changelog

All notable changes to the `knowledge-provider-ui` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-09-29

### Fixed
- `examples/knowledge-provider-ui.prod.yaml`: ingress path was `/docs-pipeline`,
  left over from before the app was rebased to "knowledge-provider". Verified
  directly against the published images' nginx configs: `v0.1.0` (previously
  pinned here) still serves `/docs-pipeline/`, but no versioned tag published
  since the rebase serves the new path - only the floating `main` tag does.
  Bumped `image.tag` to `main` and the ingress path to `/knowledge-provider`
  together so the example stays deployable and self-consistent; repin to a
  versioned tag once the app repo cuts one matching this base path.

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment, Service, ServiceAccount, env ConfigMap and
  optional Ingress, all wired through the `common` library chart.
- Liveness/readiness probes on `/health` (nginx, `ui/nginx.prod.conf`).
- `automountServiceAccountToken: false` by default - this workload calls no
  Kubernetes API.
- `envConfig` deliberately empty by default: `VITE_*` configuration is baked
  into the image at `docker build` time and unreachable from this chart - see
  README.md, "Build-time configuration".
