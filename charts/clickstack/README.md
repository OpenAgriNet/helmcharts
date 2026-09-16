# clickstack

[ClickStack](https://clickhouse.com/docs/use-cases/observability/clickstack) —
ClickHouse, an OpenTelemetry collector, and the HyperDX UI in one release. This
is where the traces, logs and metrics the adapters emit land.

**This chart is a verbatim copy of the official upstream chart.** It is
`hyperdx/clickstack` **1.1.1** (appVersion **2.8.0**), pulled from
`https://hyperdxio.github.io/helm-charts` and committed here unmodified —
`Chart.yaml`, `values.yaml`, every template, the `data/` ClickHouse XML and the
upstream `tests/` are all byte-identical to the published artifact. Only this
README and the `CHANGELOG.md` are ours.

Nothing here follows the repo's chart conventions yet, and that is deliberate:
the first commit is upstream as-published, so that any later OAN change is
visible as a diff against it rather than buried in the initial import. See
[Departures from repo conventions](#departures-from-repo-conventions) for what
that costs and what has to be overridden per environment in the meantime.

## What it renders

With upstream defaults (`helm install clickstack charts/clickstack`):

| Resource | Notes |
|---|---|
| Deployment `clickstack-clickhouse` | ClickHouse 25.7, with data and log PVCs |
| Deployment `clickstack-mongodb` | Mongo 5.0.32 — HyperDX's own metadata store |
| Deployment `clickstack-otel-collector` | The ClickStack OTel collector; where services send telemetry |
| Deployment `clickstack-app` | The HyperDX API and UI |
| Services | `-clickhouse` (8123/9000/9363), `-app` (3000, 4320 OpAMP), `-mongodb` (27017), `-otel-collector` (4317 gRPC, 4318 HTTP, 13133 health, 24225 fluentd, 8888 metrics) |
| ConfigMaps | ClickHouse config and users, HyperDX app config |
| Secrets | ClickHouse and app secrets — **rendered by the chart, from values** |
| PVCs | ClickHouse data (10Gi), ClickHouse logs (5Gi), MongoDB (10Gi) |
| Ingress | Optional, off by default |
| PodDisruptionBudget | Optional, off by default |
| CronJob | Alert checks, optional (`tasks.enabled`), off by default |

## Install

```bash
helm install clickstack charts/clickstack -n observability --create-namespace
```

There are no chart dependencies to fetch — everything is in this directory.
Name the release `clickstack` and resources read `clickstack-app`,
`clickstack-otel-collector` and so on; any other release name prefixes it.

The UI is a `ClusterIP` on 3000. Until an ingress is configured, reach it with:

```bash
kubectl -n observability port-forward svc/clickstack-app 3000:3000
```

First open of the UI asks you to create the admin account, then hands you an
ingestion API key. Connections and sources for the bundled ClickHouse are
provisioned automatically from `hyperdx.defaultConnections` and
`hyperdx.defaultSources`.

## Pointing services at the collector

[`adapter-service`](../adapter-service) takes an OTLP endpoint directly:

```yaml
otel:
  enabled: true
  endpoint: clickstack-otel-collector.observability.svc.cluster.local:4317
```

Use 4318 for OTLP/HTTP. Both ports are on the same service. Senders also need
the ingestion API key from the UI — the collector rejects unauthenticated
telemetry.

## Departures from repo conventions

Upstream is not written to this repo's [`CONVENTIONS.md`](../../CONVENTIONS.md),
and vendoring it as-published means carrying those gaps openly rather than
hiding them:

- **It renders Secrets.** `templates/secrets.yaml` builds ClickHouse and app
  Secrets out of values, and `values.yaml` ships working defaults —
  `clickhouse.config.users.appUserPassword: hyperdx`,
  `otelUserPassword: otelcollectorpass`, and a placeholder `hyperdx.apiKey`.
  Every one of those must be overridden per environment. No repo chart is
  supposed to render a Secret at all; closing this properly means moving to
  `hyperdx.useExistingConfigSecret` and externally created Secrets.
- **No `common` dependency**, so no `app.kubernetes.io/part-of: oan` label
  and none of the shared helpers.
- **No resource requests or limits by default.** `clickhouse.resources`,
  `otel.resources` and the rest are `{}`. Mandatory in this repo, and on a
  ClickHouse in particular the absence is not cosmetic — set them before this
  goes anywhere shared.
- **`global.storageClassName: local-path`**, which is upstream's k3s-friendly
  default and almost certainly not what an OAN cluster uses. Set it to the
  cluster's class or `""` for the default one.
- **Chart named after the implementation.** Conventions name a component for
  its role, which would make this `observability`. It is `clickstack` because
  that is the upstream chart name, and renaming it would be the first
  modification.
- **No `examples/*.yaml` or `ci/*-values.yaml`.** The chart lints and renders on
  its own defaults, so `scripts/lint-charts.sh` covers it, but there is no dev
  or prod values file yet.

## Refreshing from upstream

Re-vendor rather than hand-patch, for as long as this stays unmodified:

```bash
helm repo add hyperdx https://hyperdxio.github.io/helm-charts
helm repo update hyperdx
helm search repo hyperdx/clickstack --versions | head

rm -rf charts/clickstack
helm pull hyperdx/clickstack --version <new> --untar --untardir charts/
git checkout charts/clickstack/README.md charts/clickstack/CHANGELOG.md
```

Then read the diff, bump the entry in `CHANGELOG.md`, and note the new
`appVersion`. Once this chart carries OAN changes, that flow stops working and
the upgrade becomes a merge — which is the reason the initial import is clean.

## Links

- Chart source: <https://github.com/hyperdxio/helm-charts>
- HyperDX: <https://github.com/hyperdxio/hyperdx>
- ClickStack docs: <https://clickhouse.com/docs/use-cases/observability/clickstack>
