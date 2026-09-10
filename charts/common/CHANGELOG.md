# Changelog

All notable changes to the `common` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Renamed from `oan-common` to `common`. The chart directory, `name` in
  `Chart.yaml`, and the `oan-common.*` template helper prefix all became
  `common`, and every dependent chart's `dependencies` entry and
  `file://../oan-common` repository path moved with them. No helper's arguments
  or output changed, so a dependent chart needs no edit beyond the new name.

## [0.2.0] - 2026-08-31

### Added
- `common.image` now fails the render when `image.repository` is empty.
  Previously an empty repository produced a syntactically valid but meaningless
  reference such as `ghcr.io/:v2.0.0`, which Helm and the API server both accept
  and which only surfaces later as an `ImagePullBackOff` - long after the deploy
  appeared to succeed.

### Removed
- `common.externalsecret`, `common.externalSecret.enabled` and
  `common.externalSecret.targetName`, along with the `externalSecrets` value
  block. External Secrets Operator is not installed in any OAN cluster, so no
  consuming chart could exercise them.

  The helpers that reference existing Secrets - `common.env` for `secretEnv`,
  and each chart's own `*Secret.name` settings - are unchanged.

## [0.1.0] - 2026-08-31

### Added
- Initial release of the `common` library chart.
- Name, fullname, chart, standard label, selector label, annotation, and
  namespace helpers.
- Image reference and image pull secret helpers, with digest pinning
  (`image.digest`) taking precedence over `image.tag`.
- Service account name/enabled helpers.
- Env ConfigMap name/data helpers and an `envConfig` checksum annotation helper.
- `common.env`, rendering container env entries from `secretEnv` (mapping an
  env var name to a specific Secret key) and `extraEnv` (raw passthrough), and
  failing the render when a `secretEnv` entry is missing its name or key.
- `common.resources`, which fails the render when `resources` is empty so no
  component can ship without a resource contract.
- `common.probes` and `common.probeSpec`, passing every probe field
  through verbatim and failing the render when an enabled probe declares no
  handler or more than one.
- Pod-level and container-level security context helpers, gated on `enabled`.
- `common.externalsecret`, rendering a complete External Secrets Operator
  `ExternalSecret`, with target name and enabled helpers, and render-time
  validation of `secretStoreRef.name` and `data`/`dataFrom`.
- `common.waitFor`, rendering init containers that block startup until TCP
  and HTTP dependencies are reachable - the missing equivalent of compose's
  `depends_on: condition: service_healthy`. Checks are passed in explicitly so a
  consuming chart derives host and URL from its own settings rather than
  duplicating them.
- Deployment and Ingress apiVersion helpers.
