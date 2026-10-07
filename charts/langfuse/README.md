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

## What it renders

With upstream defaults (`helm install langfuse charts/langfuse`), every
bundled dependency deploys:

| Resource | Notes |
|---|---|
| Deployment `langfuse-web` | The UI and API, `ClusterIP` on 3000 |
| Deployment `langfuse-worker` | Ingestion, batch export, evaluators — no inbound Service |
| `ClickHouseCluster` / `KeeperCluster` (`clickhouse.com/v1alpha1`) | Trace/observation storage — **requires the Altinity ClickHouse Operator already installed**, the same operator relationship `postgresql-cnpg` has with CNPG |
| StatefulSet (bundled `postgres` subchart) | Langfuse's own metadata Postgres — **not** `postgresql-cnpg`; see below |
| Deployment (bundled `redis`/valkey subchart) | Queueing for the worker |
| Deployment + PVC (bundled `s3`/seaweedfs subchart) | S3-compatible storage for event/media uploads |
| Secrets | App (salt/encryption key/NextAuth), Postgres, Redis, ClickHouse, S3 — **all rendered by the chart, from values** |
| PodDisruptionBudgets, ServiceAccounts, HPAs/VPAs (optional) | One set per component |
| Ingress | Optional, off by default |

## Install

```bash
helm dependency update charts/langfuse
helm install langfuse charts/langfuse -n observability \
  -f charts/langfuse/ci/lint-values.yaml   # or your own values
```

`helm dependency update` is required before lint/template/install — the
bundled `postgres`, `redis` and `s3` subchart archives are not committed,
matching how this repo already treats `common` (`CONVENTIONS.md`).

**The Altinity ClickHouse Operator (and its cert-manager prerequisite) must
already be running in the cluster**, or the install fails fast naming exactly
that — this chart checks for the CRDs at template time via a cluster `lookup`.
There is no such check for rendering offline (CI, GitOps diff); that is what
`clickhouse.crdCheck: false` is for — see
[Departures from repo conventions](#departures-from-repo-conventions).

## Pointing the DSS at it

`decision-support-system`'s `tracing.otlpEndpoint` (see
[`../decision-support-system/README.md`](../decision-support-system/README.md#tracing))
expects an OTLP endpoint, not Langfuse's native API directly — Langfuse accepts
OTLP (ADR-0007 in the app repo). This chart does not configure that collector
hop; it only stands up Langfuse itself.

## Its own metadata Postgres, deliberately separate from `postgresql-cnpg`

Langfuse's bundled `postgres` subchart (`postgresql.deploy: true` by default)
is a plain Postgres `StatefulSet` from `groundhog2k/helm-charts` — not a CNPG
`Cluster`, and not the `postgresql-cnpg` chart this repo uses for `registry`
and `discovery`. Pointing Langfuse at a CNPG-managed instance instead means
setting `postgresql.deploy: false` and filling in `postgresql.externalHost`
(see upstream's own `examples/external-components/external-postgres.yaml` in
[`langfuse/langfuse-k8s`](https://github.com/langfuse/langfuse-k8s)) against a
`postgresql-cnpg` release created for it. That consolidation is **not done by
this import** — it is the first thing to decide before a shared-cluster
install, not something vendoring silently assumes.

## The bundled S3 store does not render under this repo's pinned Helm

`s3.deploy: true` (the upstream default) bundles the `seaweedfs` sub-chart.
Its own `templates/shared/security-configmap.yaml` calls Sprig's `fromToml` —
a function this repo's CI Helm (`v3.16.4`, pinned in
`.github/workflows/helm-lint.yml`) does not have:

```
[ERROR] templates/: parse error at (langfuse/charts/s3/templates/shared/security-configmap.yaml:21): function "fromToml" not defined
```

This is **not a CI-only quirk** — any real install of this chart through a
Helm or Argo CD toolchain old enough to lack `fromToml` fails identically,
with `s3.deploy` left at its own upstream default. `ci/lint-values.yaml` sets
`s3.deploy: false` for exactly this reason, pointed at a placeholder external
store, which happens to match what the README already recommends anyway —
reuse an existing S3-compatible store rather than bundling a private one. Set
`s3.deploy: true` only after confirming whatever Helm actually deploys this
chart is new enough to have `fromToml`.

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
  Kubernetes Operator, `ghcr.io/clickhouse/clickhouse-operator-helm` — not
  Altinity's) this repo does not otherwise depend on anywhere else, and checks
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
