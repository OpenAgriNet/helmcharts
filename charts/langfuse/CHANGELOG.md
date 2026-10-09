# Changelog

All notable changes to the `langfuse` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

**This chart's `version` tracks upstream, not this repository.** It is a verbatim
copy of `langfuse/langfuse-k8s`, so `2.1.1` here means upstream `2.1.1` and
nothing else. The moment an OAN modification lands, this chart's version has to
fork from upstream's and follow [`CONVENTIONS.md`](../../CONVENTIONS.md)
instead — record that switch in this file when it happens.

## [2.1.1] - 2026-10-01

Initial import. Upstream `langfuse/langfuse-k8s` 2.1.1 (appVersion 4.35.0),
pulled from `https://github.com/langfuse/langfuse-k8s`, committed unmodified.

### Added
- `langfuse-web` and `langfuse-worker`, with bundled `postgres` (metadata),
  `redis`/valkey (queueing) and `s3`/seaweedfs (event/media storage) subchart
  dependencies, each deployable or pointable at an external instance via its
  own `deploy` toggle.
- ClickHouse via the official ClickHouse Kubernetes Operator's
  (`ghcr.io/clickhouse/clickhouse-operator-helm`) `ClickHouseCluster` /
  `KeeperCluster` CRDs — requires that operator already running in the
  cluster.
- Upstream's own `tests/` (helm-unittest), kept so a later OAN change can be
  checked against them.
- `ci/lint-values.yaml` (not upstream) — sets `clickhouse.crdCheck: false`
  (no live cluster to check the operator CRDs against in CI) and
  `s3.deploy: false` (the pinned CI Helm, v3.16.4, lacks the Sprig `fromToml`
  function the bundled `seaweedfs` sub-chart's own template calls — a real
  incompatibility, not a CI-only workaround; see README).
- `README.md` and this changelog — the only other files in this directory
  that are not upstream's.

### Notes
- **2.1.1 chosen over the latest (2.1.3) deliberately**: its appVersion,
  4.35.0, matches the Langfuse instance already running at
  `dpg-dev.openagrinet.global`. This import describes what is actually
  deployed; upgrading to a newer chart is a separate, later decision.
- Upstream defaults are not deployable to a shared cluster as they stand: six
  Secrets render with values-based defaults, no component sets resource
  requests or limits, and the bundled Postgres is a plain `groundhog2k`
  StatefulSet, not this repo's `postgresql-cnpg`. The README lists each one;
  resolving them is the next piece of work, not something this import does.
- `helm lint --strict` passing on bare defaults is Helm-version-dependent: a
  newer Helm (bundling a Sprig with `fromToml`) is clean; this repo's pinned
  CI Helm (v3.16.4) fails on the bundled `s3` sub-chart's template with
  `function "fromToml" not defined` — confirmed by reproducing locally against
  the exact v3.16.4 binary CI uses, not just inferred from the CI log. Also
  needs a live cluster with the ClickHouse operator installed, or
  `clickhouse.crdCheck: false`. `ci/lint-values.yaml` covers both — the same
  mechanism `decision-support-system` and others already use for a chart
  whose bare defaults don't render offline.
