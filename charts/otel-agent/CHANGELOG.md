# Changelog

All notable changes to the `otel-agent` chart.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this chart adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-09-28

### Removed

- `nodes/proxy` from the ClusterRole. The kubeletstats receiver dials each
  kubelet directly on `:10250` and never uses the API server proxy, so the verb
  was unused — and it is the one that permits `exec` through the kubelet. The
  grant arrived by copying a scrape that did go through the proxy; that scrape
  has since been deleted.

### Changed

- Adopted `common`: the chart now depends on it and takes its labels from
  `common.labels` and `common.selectorLabels` rather than hand-rolling
  `app.kubernetes.io/name` and `/instance`. Every resource now carries
  `app.kubernetes.io/part-of: oan`, as CONVENTIONS.md requires.

  The selector is unchanged in value — `common.name` resolves to `.Chart.Name`
  with no `nameOverride` — so this is safe on an existing DaemonSet, whose
  selector is immutable.

- Corrected the comment on `extra_metadata_labels`. It claimed the setting
  attaches limits and requests; it does not. Only `container.id` is set, and
  utilization against limits would need the
  `k8s.container.*_limit_utilization` metrics enabled.

## [0.1.0] - 2026-09-22

### Added

- Initial chart. A DaemonSet running the OpenTelemetry collector with the
  kubeletstats receiver, one pod per node, forwarding to the ClickStack
  collector over OTLP.

  It exists because nothing else reports per-pod CPU and memory in a form the
  dashboards query: metrics-server keeps no history, and Postgres, Keycloak,
  Kong and the registry report nothing about their own containers. kubeletstats
  emits the `k8s.*` semantic-convention names those dashboards are built on.

  A DaemonSet rather than a Deployment because a kubelet can only be read from
  the node it runs on.
