# grafana-provisioning

OAN's Grafana content (dashboards, alert rules and notification routing),
delivered as ConfigMaps that the `grafana` chart's sidecars load into Grafana.

The `grafana` chart is upstream and committed unmodified, so the content, which
is OAN's, lives in a chart of ours. It isn't in infra-automation values either,
for two reasons: the quick-start loads the same files, and those values are
rendered through `tpl`, which would try to evaluate Grafana's own `{{ }}`
templates.

## Contents

| Path | Loaded by | Grafana folder | What |
|---|---|---|---|
| `dashboards/oan-discovery-overview.json` | dashboards sidecar | `OAN` | Service overview: RED, failures, discovery quality, logs |
| `dashboards/network-api.json` | dashboards sidecar | `OAN` | Network API: requests, errors and performance, from traces and logs |
| `dashboards/providers.json` | dashboards sidecar | `OAN` | Providers: publishing and discovery, from the adapters' audit logs |
| `dashboards/infra-overview.json` | dashboards sidecar | `OAN` | Infra: CPU, memory, disk, volume and network per service, from kubelet metrics |
| `alerting/functional.yaml` | alerts sidecar | `functional-alerts` | API failure rate (critical), High latency on discover (warning), Service down (critical) |
| `alerting/notifications.yaml` | alerts sidecar | n/a | The `slack` contact point, and the policy routing every alert to it |

The `functional-alerts` folder also shows up under **Dashboards**, with no
dashboards in it. That's expected: Grafana has one folder system for dashboards
and alert rules, every rule must be in a folder, and every folder is listed
there. The rules are kept in their own folder on purpose, apart from the
dashboards.

Every file becomes its own ConfigMap, rendered unchanged: `.Files.Get`, never
`tpl`. Add a dashboard by dropping its JSON into `dashboards/`. Every file
there is picked up with no values change. Bump the chart version with any
change.

`quick-start/config/grafana/provisioning/` holds byte-identical copies
(`dashboards/json/` and `alerting/`). `scripts/lint-charts.sh` fails if they
drift. The exception is `infra-overview.json`: the chart's copy reads kubelet
metrics (`k8s.pod.*`, `k8s.volume.*`) and the quick-start's reads Docker
container stats, so the two differ on purpose and are not compared.

## Install

In the same namespace as Grafana, with both sidecars enabled in the grafana
release:

```yaml
# grafana values
sidecar:
  dashboards:
    enabled: true
    label: grafana_dashboard
    labelValue: "1"
    provider:
      folder: OAN
  alerts:
    enabled: true
    label: grafana_alert
    labelValue: "1"
```

```bash
helm install grafana-provisioning charts/grafana-provisioning -n observability
```

Everything queries the datasource with uid `clickhouse`, which the grafana
values provision.

## Values

| Key | Default | Purpose |
|---|---|---|
| `dashboards.enabled` | `true` | Render every `dashboards/*.json` as a ConfigMap |
| `dashboards.sidecarLabel` | `grafana_dashboard` / `"1"` | Must match `sidecar.dashboards.label` / `labelValue` |
| `alerting.files.<name>.enabled` | `true` | Render `alerting/<name>.yaml` as a ConfigMap |
| `alerting.sidecarLabel` | `grafana_alert` / `"1"` | Must match `sidecar.alerts.label` / `labelValue` |
| `commonLabels` | `{}` | Extra labels on every ConfigMap |

## Slack

Every alert goes to one Slack channel, through an incoming webhook. The URL is
read from `SLACK_WEBHOOK_URL` on the Grafana container, never from a file:

```yaml
# grafana values
envValueFrom:
  SLACK_WEBHOOK_URL:
    secretKeyRef:
      name: grafana-slack
      key: url
```

It must not be empty: Grafana refuses to start with a Slack contact point that
has no URL. `notifications.yaml` also replaces Grafana's whole notification
policy tree, so add routes there, not in the UI.

## Removing a rule

Grafana keeps a provisioned rule in its database after it is taken out of the
file. To retire one, list its uid under `deleteRules:` in the same file.
