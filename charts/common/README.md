# common

The common Helm **library chart** for OpenAgriNet (OAN) services.

`common` renders **no resources of its own** and is never installed
directly. Service charts declare it as a dependency and call its named template
helpers, so names, labels, image references, probes, resource contracts, and
secret wiring are identical across every OAN chart.

## Using it in a chart

1. Declare the dependency in your chart's `Chart.yaml`:

   ```yaml
   dependencies:
     - name: common
       version: "0.1.x"
       repository: "file://../common"
   ```

2. Pull it in:

   ```bash
   helm dependency update charts/<your-chart>
   ```

   The packaged dependency is not committed, so re-run this after every edit to
   `common` — otherwise your chart keeps rendering against a stale copy.

3. Define thin chart-local wrappers in your `templates/_helpers.tpl` that
   delegate to the library:

   ```yaml
   {{- define "my-service.fullname" -}}
   {{- include "common.fullname" . -}}
   {{- end }}
   ```

   See [`template`](../template) for a complete, copy-ready example.

## Helpers

### Names and labels

| Helper | Purpose |
|---|---|
| `common.name` | Chart name, honoring `nameOverride` |
| `common.fullname` | Fully qualified name (`<release>-<chart>`), honoring `fullnameOverride` |
| `common.chart` | `name-version` string for the `helm.sh/chart` label |
| `common.labels` | Standard `app.kubernetes.io/*` labels, `part-of: oan`, plus `commonLabels` |
| `common.selectorLabels` | Pod/Service selector labels (name + instance) |
| `common.annotations` | Renders `commonAnnotations` |
| `common.namespace` | Release namespace |

### Workload

| Helper | Purpose |
|---|---|
| `common.image` | Full image ref from `image.registry`/`repository`/`tag`, falling back to `Chart.appVersion` then `latest`. `image.digest` pins by digest and wins over the tag. **Fails the render when `repository` is empty** |
| `common.imagePullSecrets` | Renders the `imagePullSecrets` block from `image.pullSecrets` |
| `common.resources` | Renders `resources`. **Fails the render when empty** — every OAN component must declare a resource contract |
| `common.probes` | Renders every enabled probe block (startup, liveness, readiness) for a container spec |
| `common.probeSpec` | Renders one probe. Takes `(dict "probe" <probe> "name" <name> "chart" .Chart.Name)` |
| `common.podSecurityContext` | Pod-level security context, only when `podSecurityContext.enabled` |
| `common.securityContext` | Container-level security context, only when `securityContext.enabled` |

Probes pass every field except `enabled` through verbatim, so any handler
(`httpGet`, `tcpSocket`, `exec`, `grpc`) and any timing field works. Two
render-time guardrails apply:

- An enabled probe with **no** handler fails the render.
- An enabled probe with **more than one** handler fails the render. This is the
  common trap: Helm merges maps, so overriding a default `httpGet` probe with
  `tcpSocket` leaves both in the merged value and the API server rejects it at
  apply time. Null out the default you are replacing:

  ```bash
  --set livenessProbe.tcpSocket.port=http --set livenessProbe.httpGet=null
  ```

### Service account

| Helper | Purpose |
|---|---|
| `common.serviceAccount.name` | Service account name (generated, overridden, or `default` when disabled) |
| `common.serviceAccount.enabled` | Emits `true` when a ServiceAccount should be created |

### Configuration and secrets

| Helper | Purpose |
|---|---|
| `common.env` | Container `env` entries from `secretEnv` (env var name -> secret key) and `extraEnv` (raw passthrough) |
| `common.envConfigMapName` | Name of the env ConfigMap (`<fullname>-env`) |
| `common.envConfigMapData` | Renders `envConfig` into ConfigMap `data` entries |
| `common.checksumAnnotation` | Checksum of `envConfig`, to roll pods when config changes |

### apiVersions

`common.deployment.apiVersion` and `common.ingress.apiVersion` keep those
in one place, so a Kubernetes upgrade is a single edit.

## Value schema

The helpers read the keys documented in [`values.yaml`](./values.yaml):
`nameOverride`, `fullnameOverride`, `image.*` (including `digest`), `serviceAccount.*`, `envConfig`,
`resources`, `secretEnv`, `extraEnv`, `livenessProbe`/`readinessProbe`/`startupProbe`,
`podSecurityContext`, `securityContext`, `waitFor`, `commonLabels`, and
`commonAnnotations`. A consuming chart inherits this schema and extends it with
its own keys (`replicaCount`, `service`, `ingress`, ...).

## Versioning

Consumers pin `version: "0.1.x"`. Ship helper additions as PATCH/MINOR; reserve
MAJOR for renaming or changing the behaviour of an existing helper, since that
forces every consuming chart to update its pin. Every change needs a `version`
bump in `Chart.yaml` and an entry in [`CHANGELOG.md`](./CHANGELOG.md) — see
[`CONVENTIONS.md`](../../CONVENTIONS.md).
