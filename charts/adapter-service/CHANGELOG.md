# Changelog

All notable changes to this chart are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this chart adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-09-22

### Changed
- **Signing and signature validation are on.** Each hop now has a signer on one
  side and a validator on the other, which is why this is one change and not
  three:

  | Module | Steps |
  |---|---|
  | consumer | `validateSchema, addRoute, sign` |
  | network | `validateSign, addRoute` |
  | provider (select) | `validateSign, validateSchema, <capabilities>` |
  | provider (publish) | `validateSchema, addRoute, sign` |

  Turning any one of these off on its own breaks the hop it belongs to: drop
  `sign` on consumer and both network and provider reject every request.

  The network adapter does not sign on the way out, and the provider does not
  `signAck`, because nothing verifies either: the discovery service's signature
  middleware is parked rather than mounted, and the consumer has no validation
  step on the response path. Signing for a verifier that does not exist is a
  keypair operation per request for nothing.

- **Extended schema validation is on** wherever `validateSchema` runs — consumer
  and both provider modules. It fetches each resource's own `@context` and
  validates against it, so a payload can now fail on a network call.
  `extendedSchema_allowedDomains` is what keeps that fetch from following a
  payload to an arbitrary host. Note it does not enforce `if/then`, so a pass is
  not pack conformance.

- **Telemetry is on** in all three roles. This assumes a collector answers at
  `clickstack-otel-collector.observability.svc.cluster.local:4317`. Without one
  the exporter logs a connection failure every few seconds — set all three flags
  back to `false` for a cluster with no collector.

### Known issue
- `handler.steps`, `otel.*` and `becknSpec.*` in `values.yaml` no longer affect
  anything. Since 0.3.0 the config is read from `config/<role>-config.yaml` by
  `.Files.Get`, and only `NOTES.txt` still reads those values — so the
  post-install message prints a step list the adapter is not running. Fixing it
  means either deleting the dead values or rendering the config from them.

## [0.3.1] - 2026-09-18

### Fixed
- `subscriberId` and `keyId` are quoted in all three role configs. The init
  container substitutes each sentinel with the contents of the keys Secret, and
  the placeholder `keyId` holds before registry-seed runs contains a colon, so
  the substituted line parsed as a nested mapping. Every adapter crash-looped on
  `yaml: line 77: mapping values are not allowed in this context`.

### Added
- The init container fails, naming `resolve-key-ids.sh`, when the `keyId` in the
  Secret is still the `<<PENDING ...>>` sentinel. Quoting alone would let the
  adapter start and sign with an identity the registry has never issued, which
  fails at every counterparty with nothing in this pod's logs to explain it.

## [0.3.0] - 2026-09-18

### Changed
- **BREAKING.** The adapter config comes from `config/<role>-config.yaml` in this
  chart, mounted verbatim, instead of being rendered by `configmap.yaml`. The
  render fails naming the expected file when it is absent.

  An adapter config is a plugin graph. The provider one carries a plugin per
  capability, each with its own binding key, auth block and mapping, plus a
  second module for the outbound publish leg — 298 lines against the 131 the
  template produced. Expressing that in values would mean this chart learning
  what a capability is, and every new provider becoming a chart change.

  `config` in values still overrides the file, for an environment that must
  differ without a new file.

### Added
- `keys.existingSecret.extraSubstitutions`: placeholder -> key in the same
  Secret, substituted by the same init container as the keys. Upstream token
  endpoints use it — they are deployment facts, one of them a bare IP, and
  committing them would put a partner's endpoint in a public repository.

### Fixed
- The leftover-placeholder check matched only `__ADAPTER_`, so any other
  unsubstituted placeholder reached the application as literal text and failed
  as a DNS error on a hostname like `__AGMARKNET_TOKEN_URL__`. It now catches
  any `__UPPERCASE__` and refuses to start.
- `examples/consumer.yaml` still said `role: experience` throughout. The file
  was renamed in 845d916 but its contents were not, so it failed to render
  against the chart that validates `consumer`.

## [0.2.0] - 2026-09-17

### Changed
- **BREAKING.** The third role is `consumer`, not `experience`. `role: experience`
  now fails the render, `examples/experience.yaml` is `examples/consumer.yaml`,
  and the default `appName` and handler wiring follow.

  The compose stack renamed this adapter to `consumer-adapter`, and the chart
  kept validating against the old word -- so a values file written from the
  running stack was rejected with "role must be one of provider, network,
  experience", which names the rule rather than the rename. The two now agree.

  The role is not cosmetic: it decides the handler role, the step list and the
  routing target, so a chart that accepted both spellings would be a chart with
  two names for one thing.

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
