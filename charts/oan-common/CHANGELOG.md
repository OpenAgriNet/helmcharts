# Changelog

All notable changes to the `oan-common` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-31

### Added
- `oan-common.image` now fails the render when `image.repository` is empty.
  Previously an empty repository produced a syntactically valid but meaningless
  reference such as `ghcr.io/:v2.0.0`, which Helm and the API server both accept
  and which only surfaces later as an `ImagePullBackOff` - long after the deploy
  appeared to succeed.

### Removed
- `oan-common.externalsecret`, `oan-common.externalSecret.enabled` and
  `oan-common.externalSecret.targetName`, along with the `externalSecrets` value
  block. External Secrets Operator is not installed in any OAN cluster, so no
  consuming chart could exercise them.

  The helpers that reference existing Secrets - `oan-common.env` for `secretEnv`,
  and each chart's own `*Secret.name` settings - are unchanged.

## [0.1.0] - 2026-08-31

### Added
- Initial release of the `oan-common` library chart.
- Name, fullname, chart, standard label, selector label, annotation, and
  namespace helpers.
- Image reference and image pull secret helpers, with digest pinning
  (`image.digest`) taking precedence over `image.tag`.
- Service account name/enabled helpers.
- Env ConfigMap name/data helpers and an `envConfig` checksum annotation helper.
- `oan-common.env`, rendering container env entries from `secretEnv` (mapping an
  env var name to a specific Secret key) and `extraEnv` (raw passthrough), and
  failing the render when a `secretEnv` entry is missing its name or key.
- `oan-common.resources`, which fails the render when `resources` is empty so no
  component can ship without a resource contract.
- `oan-common.probes` and `oan-common.probeSpec`, passing every probe field
  through verbatim and failing the render when an enabled probe declares no
  handler or more than one.
- Pod-level and container-level security context helpers, gated on `enabled`.
- `oan-common.externalsecret`, rendering a complete External Secrets Operator
  `ExternalSecret`, with target name and enabled helpers, and render-time
  validation of `secretStoreRef.name` and `data`/`dataFrom`.
- `oan-common.waitFor`, rendering init containers that block startup until TCP
  and HTTP dependencies are reachable - the missing equivalent of compose's
  `depends_on: condition: service_healthy`. Checks are passed in explicitly so a
  consuming chart derives host and URL from its own settings rather than
  duplicating them.
- Deployment and Ingress apiVersion helpers.
