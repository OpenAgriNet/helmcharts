# Changelog

All notable changes to the `clickstack` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

**This chart's `version` tracks upstream, not this repository.** It is a verbatim
copy of `hyperdx/clickstack`, so `1.1.1` here means upstream `1.1.1` and nothing
else. The moment an OAN modification lands, this chart's version has to fork from
upstream's and follow [`CONVENTIONS.md`](../../CONVENTIONS.md) instead — record
that switch in this file when it happens.

## [1.1.1] - 2026-09-09

Initial import [#4]. Upstream `hyperdx/clickstack` 1.1.1 (appVersion 2.8.0),
pulled from `https://hyperdxio.github.io/helm-charts`, committed unmodified.

### Added
- ClickHouse 25.7, MongoDB 5.0.32, the ClickStack OTel collector
  (`docker.clickhouse.com/clickhouse/clickstack-otel-collector`) and the HyperDX
  2.8.0 app, with the collector exposing OTLP on 4317 (gRPC) and 4318 (HTTP).
- Upstream's own `tests/` (helm-unittest), kept so a later OAN change can be
  checked against them.
- `README.md` and this changelog — the only two files in this directory that are
  not upstream's.

### Notes
- Chosen over the `hdx-oss-v2` chart, which is the same project under its
  previous name and stops at 0.8.4 / appVersion 2.7.1. The finternet deployment
  runs a fork of `hdx-oss-v2` 0.10.0; this is the current line, and nothing from
  that fork — the bootstrap Job, the ingestion-key Secret, the
  `clickhouse-backup` sidecar — was carried over.
- Upstream defaults are not deployable to a shared cluster as they stand:
  ClickHouse and OTel passwords ship as working defaults in `values.yaml`, no
  component sets resource requests or limits, and `global.storageClassName` is
  `local-path`. The README lists each one; overriding them is the next piece of
  work, not something this import does.
- `helm lint --strict` and `helm template` both pass on upstream defaults, so
  `scripts/lint-charts.sh` covers the chart without a `ci/*-values.yaml`.
