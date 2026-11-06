# Changelog

All notable changes to the `decision-support-system` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-04

Brings the chart up to `OpenAgriNet/decision-support-system` `main`
(`src/dss/config/settings.py` and `docker-compose.yml`). The new settings
need an image built after `v0.1.0`; `appVersion` stays `v0.1.0` until the
app cuts a newer tag.

### Changed
- **Breaking:** `network.receiverId` is now `network.hostId`, rendered as
  `DSS_NETWORK_HOST_ID`. The app renamed the setting and no longer reads
  `DSS_NETWORK_RECEIVER_ID`, so the old value was being dropped silently.
  The render fails if `network.receiverId` is still set.
- The render fails if `DSS_STUB_LLM` or `DSS_TRACE_INCLUDE_MESSAGE_CONTENT`
  is `"true"` in `envConfig` or `extraEnv`. Both were documented as never-in-a-
  deployment; now the chart enforces it.

### Added
- `network.senderFromTurnEnabled` → `DSS_NETWORK_SENDER_FROM_TURN_ENABLED`.
- `providerPreferences.{baseUrl,timeoutSeconds}` →
  `DSS_PROVIDER_PREFERENCES_*` (ADR-0019, ADR-0020).
- `photon.{baseUrl,timeoutSeconds,countryCodes,cache.*}` → `DSS_PHOTON_*`.
- `schemes.configPath` → `DSS_SCHEMES_CONFIG_PATH`, defaulting to the
  catalog in the image as `docker-compose.yml` does (ADR-0021).
- `redaction.{enabled,configPath}` → `DSS_REDACTION_*` (ADR-0015); the render
  fails if it is enabled with no path.
- `edgeTranslation.configPath` → `DSS_EDGE_TRANSLATION_CONFIG_PATH` (#174).
- `files` and `filesMountPath`: a ConfigMap of files mounted read-only, for
  settings that take a path (translation, schemes, redaction rules). A
  `checksum/files` annotation rolls the pod when they change.
- `modelProfile` → `DSS_MODEL_PROFILE`.
- `DSS_RELEASE`, from `image.tag` or `appVersion`, so `context.version`
  names the build that answered. `envConfig.DSS_RELEASE` overrides it.
- `tracing.serviceName` → `OTEL_SERVICE_NAME` (default `dss`),
  `tracing.resourceAttributes` → `OTEL_RESOURCE_ATTRIBUTES`,
  `tracing.metricsTemporality` →
  `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`.
- `ci/full-values.yaml`, rendering every optional setting at once.

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
- Render-time guards (`_helpers.tpl`) for the footguns the app itself only
  catches at process boot, one agent at a time: an `azure:` model with no
  `azureOpenai.endpoint`/`apiKeySecret`, a non-`azure:` (openai:) model with
  no `openai.apiKeySecret`, and a provider network with only one of
  `discoveryBaseUrl`/`invocationBaseUrl` set (which the app silently treats
  as fully unwired rather than failing, per `Settings.network_enabled`).
  `ci/lint-values.yaml` now supplies `openai.apiKeySecret` so the chart's own
  `openai:` defaults still lint/render.
- `examples/decision-support-system.litellm.yaml`: the `openai:` model path
  repointed at a self-hosted, GPU-hosted model served through a LiteLLM
  proxy via `openai.baseUrl`, rather than api.openai.com. No template change
  needed — LiteLLM's proxy speaks the OpenAI API, so this was already
  possible; it just was not documented anywhere in the chart.
- Standardized the model-provider API key Secret name to `dss-api` (was
  `dss-openai`/`dss-azure-openai`) in the README install snippet,
  `examples/decision-support-system.{dev,prod}.yaml` and
  `ci/lint-values.yaml`, matching `dss-langfuse`/`dss-litellm` elsewhere.
  `ci/azure-network-values.yaml` keeps two distinct names on purpose — it
  wires an `azure:` agent and an `openai:` agent at once, which genuinely
  needs two separate Secrets.
- Added `gateway.{url,apiKeySecret}` for the model gateway
  (`OpenAgriNet/decision-support-system` ADR-0013): `models.*` becomes fixed
  gateway-resolved labels (`dss-intent`, ...) instead of `openai:`/`azure:`
  strings once `gateway.url` is set, wired to `DSS_GATEWAY_URL`/
  `DSS_GATEWAY_API_KEY`. `entrypoint/composition.py::_resolve_model` checks
  `gateway_url` before the `azure:` prefix, so `usesAzure`/`usesOpenai` and
  their `azureOpenai.*`/`openai.*` env no longer apply once the gateway is
  configured — `_helpers.tpl` now skips them via a new `usesGateway` guard,
  which itself requires `gateway.apiKeySecret.name`. This chart does not
  deploy the gateway (LiteLLM + its own Postgres) — that is a separate
  component. `examples/decision-support-system.litellm.yaml`, which
  previously guessed at a consumer-side `openai.baseUrl` pattern, is
  rewritten to the real ADR-0013 mechanism. New
  `ci/gateway-values.yaml` covers the branch.
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
- `appVersion` was `1.0.0`, which does not match any tag the app repo has
  actually cut; corrected to `v0.1.0`. `examples/decision-support-system.dev.yaml`
  hardcoded `image.tag: "1.0.0"` for the same wrong version and is corrected
  to `"v0.1.0"` too.
