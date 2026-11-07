# Changelog

All notable changes to the `litellm` chart are documented here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this chart
follows [Semantic Versioning](https://semver.org/).

## [0.2.1] - 2026-11-07

### Changed

- A model's price may now be `0`, for a self-hosted model nobody bills per
  token. It still has to be written down: a model with no price fails the
  render as before.

## [0.2.0] - 2026-11-05

### Changed

- **Breaking:** renamed from `model-gateway` (directory `charts/lite-llm`) to
  `litellm` (directory `charts/litellm`), named after the product. Template
  helpers are now `litellm.*`; a release named `litellm` gets a Service, and
  an `OTEL_SERVICE_NAME`, of `litellm`.

### Added

- The gateway's Postgres is now part of this chart: a CloudNativePG `Cluster`
  `litellm-db` from a copy of this repo's `postgresql-cnpg` chart (alias
  `postgresCluster`). `database.urlSecret` defaults to the Secret CNPG
  generates for it, `litellm-db-app`. Disable it to use a Postgres of your own.
- Every dependency, including a copy of this repo's `common`, is vendored under
  `charts/` with an empty `repository`.

## [0.1.1] - 2026-10-12

### Changed

- Deleting models and credentials is now opt-in (`reconcile.prune`, default
  `false`). By default the reconcile Job logs what it would remove.

### Added

- Stale credentials are pruned when `reconcile.prune` is true, so removing a
  leaked key from `credentials` can take it out of the gateway.
- `reconcile.activeDeadlineSeconds` (540) and `--timeout 10m` in the install
  command, because Helm's 5 minute default can expire while the Job waits for
  the gateway.

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

[0.1.1]: https://github.com/OpenAgriNet/helmcharts/releases/tag/model-gateway-0.1.1
[0.1.0]: https://github.com/OpenAgriNet/helmcharts/releases/tag/model-gateway-0.1.0
