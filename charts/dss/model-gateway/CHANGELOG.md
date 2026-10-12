# Changelog

All notable changes to the `model-gateway` chart are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this chart
follows [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-09-25

### Added

- First version of the chart: the model gateway every model call passes
  through, implemented with LiteLLM. Deployment, Service, ServiceAccount, env
  ConfigMap, PodDisruptionBudget and a connection test.
- `models` — what each name the assistant asks for resolves to. Applied through
  the gateway's API by a `post-install,post-upgrade` Job, so a model change
  takes effect without restarting the gateway or the assistant.
- `credentials` — one vendor account per entry, each naming a Secret. The chart
  renders no Secret and holds no key.
- Admin screen off by default, so this chart is the only way the model list
  changes.
- `blockUnpricedModels`, on by default: a call to a model with no price is
  rejected rather than recorded as free.
- Render-time guardrails for the four mistakes that fail quietly: no salt key,
  a model with no price, more than one replica without Redis, and an empty
  model list.

[0.1.0]: https://github.com/OpenAgriNet/helmcharts/releases/tag/model-gateway-0.1.0
