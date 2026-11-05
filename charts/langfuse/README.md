# langfuse

[Langfuse](https://langfuse.com/) — the self-hosted LLM observability platform
`decision-support-system` traces every agent call to (`DSS_ARCHITECTURE.md`
§6.3, ADR-0007). This is where a turn's intent, moderation, planning and
composition spans land, with full message content, for debugging and quality
review.

**This chart is a verbatim copy of the official upstream chart.** It is
`langfuse/langfuse-k8s` **2.1.1** (appVersion **4.35.0**), pulled from
`https://github.com/langfuse/langfuse-k8s` and committed here unmodified —
`Chart.yaml`, `values.yaml`, every template and the upstream `tests/` are all
byte-identical to the published artifact. Only `README.md`, `CHANGELOG.md` and
`ci/lint-values.yaml` are ours — the last one exists only so this repo's
offline CI can render the chart; see
[Departures from repo conventions](#departures-from-repo-conventions).

**2.1.1 was chosen deliberately, not the latest (2.1.3 at the time of this
import).** It is the release whose appVersion (4.35.0) matches the Langfuse
instance already running at `dpg-dev.openagrinet.global` — vendoring the chart
that produced what is actually deployed, rather than the newest one, so this
import describes the running system instead of silently proposing an upgrade.

## Everything Langfuse needs is in this directory

This is one product chart. Its sub-charts live under `charts/`, all committed,
so nothing is fetched at render time:

| Sub-chart (alias) | Source | What it runs |
|---|---|---|
| `postgresql-cnpg` (`postgresCluster`) | copy of this repo's `charts/postgresql-cnpg` | Langfuse's metadata Postgres, a CloudNativePG `Cluster` named `langfuse-db` |
| `minio` (`objectStorage`) | copy of this repo's `charts/minio` | S3 store for events, media and exports, `langfuse-minio`, bucket `langfuse` |
| `valkey` (`redis`) | upstream valkey-io, vendored | queue and cache |
| `clickhouse-operator-helm` (`clickhouse-operator`) | upstream, vendored | the operator that runs ClickHouse and Keeper |
| `common` | copy of this repo's `charts/common` | the helpers the two copies above use |

The copies drop their own `dependencies:` on `common`: Helm template names are
global across a chart and its sub-charts, so the one `common` here serves both.
For the same reason upstream valkey's `common.image` helper is renamed
`valkey.commonImage`, so it does not clash with OAN's `common.image`.

**When a shared chart changes**, copy it here again (`Chart.yaml`, `values.yaml`,
`templates/`), remove its `dependencies:` block, run `helm dependency update`
and commit.

## What it renders

| Resource | Notes |
|---|---|

| Deployment `langfuse-web` | The UI and API, `ClusterIP` on 3000 |
| Deployment `langfuse-worker` | Ingestion, batch export, evaluators — no inbound Service |
| `ClickHouseCluster` / `KeeperCluster` (`clickhouse.com/v1alpha1`) | Trace/observation storage — **needs the ClickHouse operator, bundled in this chart as a subchart**, the same operator relationship `postgresql-cnpg` has with CNPG |
| CloudNativePG `Cluster` `langfuse-db` (`postgresCluster`) | Metadata Postgres; CNPG generates the owner's password into `Secret/langfuse-db-app`, which Langfuse reads |
| Deployment + PVC `langfuse-minio` (`objectStorage`), bucket-init Job | S3 store; needs `objectStorage.credentialsSecret.name`, and `s3.accessKeyId`/`s3.secretAccessKey` pointing at the same Secret |
| Deployment (`redis`/valkey sub-chart) | Queueing for the worker |
| Secrets | Rendered from values only where no `existingSecret`/`secretKeyRef` is given |
| PodDisruptionBudgets, ServiceAccounts, HPAs/VPAs (optional) | One set per component |
| Ingress | Optional, off by default |

## Install

```bash
helm install langfuse charts/langfuse -n langfuse \
  -f charts/langfuse/ci/lint-values.yaml   # or your own values
```

All sub-charts are committed under `charts/` with `Chart.lock`, every one with an empty `repository`. Argo CD renders from a git clone with no `helm dependency build` step, so a dependency missing from `charts/` would fail the render even when its condition is false.

**The ClickHouse operator is bundled** as the `clickhouse-operator-helm` subchart
(alias `clickhouse-operator`), so nothing else has to be installed; only cert-manager
must exist, for the operator's webhook certificate. Its CRDs are installed by this same
release, so under Argo CD set `clickhouse.crdCheck: false`: the check looks for the
CRDs at render time, before this release has created them. Set `clickhouse-operator.enabled: false`
to use an operator you already run. Removing this release removes the operator and
its CRDs; deleting a CRD deletes every cluster of that kind.
There is no such check for rendering offline (CI, GitOps diff); that is what
`clickhouse.crdCheck: false` is for — see
[Departures from repo conventions](#departures-from-repo-conventions).

## Pointing the DSS at it

`decision-support-system`'s `tracing.otlpEndpoint` (see
[`../decision-support-system/README.md`](../decision-support-system/README.md#tracing))
expects an OTLP endpoint, not Langfuse's native API directly — Langfuse accepts
OTLP (ADR-0007 in the app repo). This chart does not configure that collector
hop; it only stands up Langfuse itself.

## Postgres and S3 come from this repo's charts, not upstream's

Upstream bundles a groundhog2k Postgres `StatefulSet` and SeaweedFS. Both are
removed: Postgres is a CloudNativePG `Cluster` like every other OAN database,
and S3 is MinIO like every other OAN store. (SeaweedFS also needed Sprig's
`fromToml`, which the Helm this repo pins does not have.) Langfuse is wired to
them as if they were external, so `postgresql.deploy` and `s3.deploy` stay
`false`; the render fails if either is set to `true`.

## Departures from repo conventions

Upstream is not written to this repo's [`CONVENTIONS.md`](../../CONVENTIONS.md),
and vendoring it as-published means carrying those gaps openly rather than
hiding them:

- **It renders Secrets** — six of them, from values. `langfuse.salt`,
  `langfuse.encryptionKey` and `langfuse.nextauth.secret` all support a
  `secretKeyRef` to an externally created Secret instead; none of this chart's
  defaults are safe to deploy unchanged.
- **No `common` dependency**, so no `app.kubernetes.io/part-of: oan` label and
  none of the shared helpers.
- **No resource requests or limits by default** on `web`, `worker`, or any
  bundled subchart. Mandatory in this repo; set them before this goes anywhere
  shared.
- **Bundles its own Postgres, Redis and S3-compatible store** rather than
  reusing this repo's `postgresql-cnpg` or any shared object storage — see
  above. Each has a `deploy: false` escape hatch upstream already built in.
  The S3 one isn't actually optional for this repo — see above.
- **Requires a cluster-installed operator** (the official ClickHouse
  Kubernetes Operator, bundled here as a subchart — not
  Altinity's), and checks
  for it with a live `lookup` at template time — which is also why
  `ci/lint-values.yaml` exists (see below), since `scripts/lint-charts.sh`
  renders offline.
- **Chart named after the implementation**, not a role — consistent with
  `clickstack`'s own departure for the same reason: renaming it would be the
  first modification to an otherwise-verbatim import.
- **`ci/lint-values.yaml` is not upstream.** Added so this repo's CI can
  `helm template` without a live cluster (`clickhouse.crdCheck: false`,
  upstream's own documented offline-rendering flag) and without the pinned
  Helm's missing `fromToml` (`s3.deploy: false`, see above). `clickstack`
  needed no such file because its bare defaults already render clean; this
  chart's do not, for two unrelated reasons.
- **No `examples/*.yaml`.** No dev/prod values file yet — the first real
  install is where the Postgres/Redis/S3 and secret decisions above actually
  get made, and an example written ahead of that would just be guessed.

## Refreshing from upstream

Re-vendor rather than hand-patch, for as long as this stays unmodified:

```bash
helm repo add langfuse https://langfuse.github.io/langfuse-k8s
helm repo update langfuse
helm search repo langfuse/langfuse --versions | head

rm -rf charts/langfuse
helm pull langfuse/langfuse --version <new> --untar --untardir charts/
git checkout charts/langfuse/README.md charts/langfuse/CHANGELOG.md charts/langfuse/ci
```

Then read the diff, bump the entry in `CHANGELOG.md`, and note the new
`appVersion`. Once this chart carries OAN changes, that flow stops working and
the upgrade becomes a merge — which is the reason the initial import is clean.

## Links

- Chart source: <https://github.com/langfuse/langfuse-k8s>
- Langfuse: <https://langfuse.com/>
- Self-hosting docs: <https://langfuse.com/self-hosting>
