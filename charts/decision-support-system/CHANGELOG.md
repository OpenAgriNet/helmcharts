# Changelog

All notable changes to the `decision-support-system` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-22

### Added
- Initial release: Deployment, Service, ServiceAccount, env ConfigMap,
  optional Ingress/HPA/PodDisruptionBudget, wired through the `common`
  library chart. Modeled on this repo's `discovery` chart rather than the
  bare `template`, since both are stateless HTTP services with no
  cross-replica coordination.
- Structured values for what actually varies per deployment: `models.*` (the
  four ADR-0004 agent bindings), `network.*` (the gated OAN discovery/
  invocation wiring), `schemaPacks.*`, `azureOpenai.*`/`openai.*`,
  `tracing.*` (OTLP). Everything else in `src/dss/config/settings.py` is left
  to free-form `envConfig`.
- Render-time guards (`_helpers.tpl`) for the two footguns the app itself
  only catches at process boot: an `azure:` model with no
  `azureOpenai.endpoint`/`apiKeySecret`, and a provider network with only one
  of `discoveryBaseUrl`/`invocationBaseUrl` set (which the app silently
  treats as fully unwired rather than failing, per
  `Settings.network_enabled`).
- `emptyDir` volumes for the schema-pack cache and the evidence directory -
  both explicitly ephemeral, matching the app's own documented design
  (`docs/RUNNING.md`: evidence is "a stand-in" for an API that does not exist
  yet; schema packs are fetched fresh whenever the mounted directory is
  empty).
- Probes and the `helm test` check point at `/openapi.json`, the only route
  that is always 200 once the ASGI app is constructed - there is no
  dedicated `/health` or `/readyz`. README documents this as a real
  readiness gap: `Settings.ready=False` only affects `/v1/turns` responses,
  never a probe route a k8s readinessProbe could observe.
- `securityContext`/`podSecurityContext` disabled by default, matching
  `decision-support-system/Dockerfile`'s actual posture (`python:3.13-slim`,
  no `USER` directive - the process runs as root in the image today).

### Fixed
- NOTES.txt's sample `/v1/turns` curl body used `context.envelopeVersion`,
  copied from `docs/RUNNING.md`'s own inline example - installing this chart
  against a real cluster and sending that body got `422 extra_forbidden`.
  The field is `context.version`; `docs/api-contracts/examples/
  answered_streaming.json` (the body CI actually validates responses
  against) has it right. NOTES.txt now points at that file instead of
  inlining a body that can drift again.
