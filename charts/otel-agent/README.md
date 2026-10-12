# otel-agent

A per-node OpenTelemetry collector that reads each kubelet and forwards pod,
container and node metrics to the ClickStack collector.

## Why it exists

Nothing else reports per-pod CPU and memory in a form the dashboards can query.

- metrics-server answers `kubectl top` but keeps no history, so a run that
  finished an hour ago cannot be looked at
- Postgres, Keycloak, Kong and the registry report nothing about their own
  containers and never will

kubeletstats emits the `k8s.*` semantic-convention names — `k8s.pod.cpu.usage`
and friends — which is what HyperDX's Kubernetes dashboard is built on.

A DaemonSet rather than a Deployment, because a kubelet can only be read from
the node it runs on.

## What it needs

Its own ServiceAccount, created by this chart, bound to a ClusterRole granting
`get`, `list` and `watch` on:

| Resource | For |
|---|---|
| `nodes/stats` | the metrics themselves |
| `nodes/pods` | the kubelet's `/pods` endpoint, called because `extra_metadata_labels` is set |
| `nodes`, `pods`, `namespaces` | the metadata the receiver attaches via `k8s_api_config` — without it metrics arrive with no pod or namespace name |

**`nodes/pods`, not `nodes/proxy`.** The kubelet accepts either for `/pods`, but
`nodes/proxy` also reaches every other kubelet endpoint, `exec` among them.

The `pods` and `namespaces` grants do not cover this: they are checked by the
API server, and the kubelet authorizes on its own path. Without `nodes/pods`
every scrape fails with `Forbidden ... subresource(s)=[pods proxy]`.

## Installing

```bash
helm dependency update charts/otel-agent
helm upgrade --install otel-agent charts/otel-agent \
  -n observability --create-namespace \
  --set hyperdx.apiKey=<key>
```

It forwards to the ClickStack collector rather than writing to ClickHouse
itself, so one place owns the schema, the credentials and the retries.

## Values

| Key | Default | What it does |
|---|---|---|
| `image.repository` | `otel/opentelemetry-collector-contrib` | the collector image |
| `image.tag` | `0.161.0` | pinned; a moving tag makes two runs incomparable |
| `collectionInterval` | `15s` | how often each kubelet is read |
| `exporter.endpoint` | the ClickStack collector | where metrics are sent |
| `hyperdx.apiKey` | — | bearer token the collector's OTLP receiver demands |
| `resources` | 50m / 96Mi requested | it runs on the nodes under test, so it is capped deliberately |
| `tolerations` | `[]` | control-plane nodes are EKS-managed and not visible, so none are needed |

## Notes

`extra_metadata_labels` is set to `container.id` only. Despite the name it does
**not** carry limits or requests — usage against what a pod was given comes from
the `k8s.container.*_limit_utilization` and `*_request_utilization` metrics,
which are off by default and would have to be enabled under `metrics:`.
