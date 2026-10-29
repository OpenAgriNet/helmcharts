# Changelog

All notable changes to the `otel-agent` chart.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this chart adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-29

### Added

- Pod logs. A `filelog` receiver reads `/var/log/pods` on each node (mounted
  read-only), `k8sattributes` adds the owning Deployment, StatefulSet,
  DaemonSet or Job, and that name becomes `service.name`, falling back to the
  container name. Before this only services that export OTLP themselves showed
  up in HyperDX. On by default; `logs.enabled: false` turns it off.
- `logs.exclude`: file globs to skip. Defaults to the agent's own pods.

### Changed

- With logs on, the container runs as uid 0, which pod log files require
  (root:root 0640). It keeps all capabilities dropped and no privilege
  escalation.
- The ClusterRole can read `apps/replicasets`, which `k8sattributes` needs to
  name a pod's Deployment.

## [0.1.3] - 2026-09-29

### Changed

- The ingestion-key Secret's comment says the rest of the secrets come from
  OpenBao, not AWS Secrets Manager. Comment-only; nothing rendered changes.

## [0.1.2] - 2026-09-28

### Changed

- `nodes/proxy` swapped for `nodes/pods` in the ClusterRole. Both satisfy the
  kubelet for the `/pods` endpoint the receiver calls when
  `extra_metadata_labels` is set, but `nodes/proxy` also reaches every other
  kubelet endpoint, `exec` among them. `nodes/pods` is the narrow half.

  `nodes/pods` is required, not optional: without it every scrape fails with
  `Forbidden (verb=get, resource=nodes, subresource(s)=[pods proxy])`. The
  `pods` and `namespaces` grants are for the API server and do not satisfy the
  kubelet, which authorizes on a different path.

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
