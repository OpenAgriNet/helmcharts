# dashboards

The OAN Grafana dashboards, as one ConfigMap each, for the `grafana` chart's
dashboard sidecar to load into the **OAN** folder. Deploys no workload.

| Dashboard | uid | Reads |
|---|---|---|
| Infra — Resource Usage | `infra-overview` | kubelet metrics |
| Network API — Requests, Errors and Performance | `network-api` | traces and logs |
| Providers — Publishing and Discovery | `providers` | the adapters' audit logs |

What each panel means is in
[`quick-start/config/grafana/DASHBOARDS.md`](../../quick-start/config/grafana/DASHBOARDS.md).

## Install

In the namespace Grafana runs in. The sidecar watches only its own namespace
unless `sidecar.dashboards.searchNamespace` says otherwise.

```bash
helm install dashboards charts/dashboards -n observability
```

Grafana picks a change up within a few seconds of `helm upgrade`, with no
restart.

## What the Grafana release needs

The `grafana` chart is vendored unmodified, so these go in its per-environment
values:

```yaml
plugins:
  - grafana-clickhouse-datasource@4.21.3      # name@version, not "name version"

sidecar:
  dashboards:
    enabled: true
    label: grafana_dashboard                  # = sidecar.label here
    labelValue: "1"                           # = sidecar.labelValue
    folderAnnotation: grafana_folder          # = sidecar.folderAnnotation
    provider:
      foldersFromFilesStructure: true         # the annotation's folder becomes a Grafana folder
      allowUiUpdates: false                   # the ConfigMap is the source

envValueFrom:                                 # from a Secret; no chart renders one
  CLICKHOUSE_USERNAME:
    secretKeyRef: {name: clickhouse, key: username}
  CLICKHOUSE_PASSWORD:
    secretKeyRef: {name: clickhouse, key: password}

datasources:
  datasources.yaml:
    apiVersion: 1
    datasources:
      - name: ClickHouse
        uid: clickhouse                       # every panel names this uid
        type: grafana-clickhouse-datasource
        access: proxy
        isDefault: true
        editable: false
        jsonData:
          host: clickhouse.observability.svc.cluster.local
          port: 9000
          protocol: native
          defaultDatabase: otel
          username: ${CLICKHOUSE_USERNAME}
        secureJsonData:
          password: ${CLICKHOUSE_PASSWORD}
```

## What the OTel Collector needs

Every query names its tables as `otel.<table>`, as in quick-start, so the
clickhouse exporter must write to database **`otel`**. Its own default is
`default`, so set `database: otel` on the exporter.

**Network API and Providers** read `otel_traces` and `otel_logs`: the services'
OTLP traces and logs.

**Infra** reads the kubelet, through the `opentelemetry-collector` chart in
`daemonset` mode:

```yaml
mode: daemonset
presets:
  kubeletMetrics:
    enabled: true        # k8s.pod.cpu.usage, memory, filesystem, network
  kubernetesAttributes:
    enabled: true        # service.name and the owning Deployment/StatefulSet
config:
  receivers:
    kubeletstats:
      metric_groups: [container, pod, node, volume]   # volume: for Volume usage
  service:
    pipelines:
      metrics:
        receivers: [kubeletstats]                     # plus otlp, if the services send metrics
        processors: [k8s_attributes, memory_limiter, batch]
        exporters: [clickhouse]
```

Without `kubernetesAttributes`, services show under their Deployment or pod
name instead. Without the `volume` group, *Volume usage* stays empty and the
other Infra panels still work.

Two things the kubelet does that show on the dashboard:

- **Volume usage needs a volume that reports its usage.** Cloud block storage
  (EBS, Persistent Disk, Azure Disk) does. A `hostPath`-backed PV, such as
  kind's or k3s's default `local-path` storage class, reports nothing, so the
  panel is empty on those clusters.
- **A `hostNetwork` pod reports the node's traffic** as its own in *Network
  I/O* -- kube-proxy, the CNI and the control-plane pods. That is the
  kubelet's figure, not a dashboard error; pick a namespace to leave them out.

## How Infra differs from quick-start

Docker reports a container's stats, and the kubelet a pod's, so the Infra
dashboard is the one file that differs from quick-start:

| Panel | quick-start (Docker) | Kubernetes (kubelet) |
|---|---|---|
| CPU usage | `container.cpu.utilization` | `k8s.pod.cpu.usage` × 100 |
| Memory usage | `container.memory.usage.total` | `k8s.pod.memory.working_set` |
| Disk | I/O per second, and database size | *Disk usage* (`k8s.pod.filesystem.usage`) and *Volume usage* (`k8s.volume.*`, % of each PVC in use) |
| Network I/O | `container.network.io.usage.*` | `k8s.pod.network.io` |
| Database connections | `postgresql.*` | not shown: the kubelet does not report it |

It also has a **Namespace** filter. A service is the `service.name` the
k8sattributes processor sets (the release name, through
`app.kubernetes.io/instance`), else the owning workload, else the pod.

## Values

| Key | Default | What it is |
|---|---|---|
| `sidecar.label` / `sidecar.labelValue` | `grafana_dashboard` / `"1"` | The label the sidecar selects on |
| `sidecar.folderAnnotation` | `grafana_folder` | The annotation that names the folder |
| `folder` | `OAN` | The Grafana folder |

Every JSON file in `files/` becomes one ConfigMap, so adding a dashboard is
adding a file.

## Changing a dashboard

`files/network-api.json` and `files/providers.json` are copies of the
quick-start files, because Helm cannot read outside a chart. Edit the
quick-start file, test it there, then copy it here.
`./scripts/lint-charts.sh` fails while the two differ. Bump `version` and add
a `CHANGELOG.md` entry with every change.
