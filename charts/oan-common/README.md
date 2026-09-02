# oan-common

The common Helm **library chart** for OpenAgriNet (OAN) services.

`oan-common` renders **no resources of its own** and is never installed
directly. Service charts declare it as a dependency and call its named template
helpers, so names, labels, image references, probes, resource contracts, and
secret wiring are identical across every OAN chart.

## Using it in a chart

1. Declare the dependency in your chart's `Chart.yaml`:

   ```yaml
   dependencies:
     - name: oan-common
       version: "0.1.x"
       repository: "file://../oan-common"
   ```

2. Pull it in:

   ```bash
   helm dependency update charts/<your-chart>
   ```

   The packaged dependency is not committed, so re-run this after every edit to
   `oan-common` — otherwise your chart keeps rendering against a stale copy.

3. Define thin chart-local wrappers in your `templates/_helpers.tpl` that
   delegate to the library:

   ```yaml
   {{- define "my-service.fullname" -}}
   {{- include "oan-common.fullname" . -}}
   {{- end }}
   ```

   See [`oan-template`](../oan-template) for a complete, copy-ready example.

## Helpers

### Names and labels

| Helper | Purpose |
|---|---|
| `oan-common.name` | Chart name, honoring `nameOverride` |
| `oan-common.fullname` | Fully qualified name (`<release>-<chart>`), honoring `fullnameOverride` |
| `oan-common.chart` | `name-version` string for the `helm.sh/chart` label |
| `oan-common.labels` | Standard `app.kubernetes.io/*` labels, `part-of: oan`, plus `commonLabels` |
| `oan-common.selectorLabels` | Pod/Service selector labels (name + instance) |
| `oan-common.annotations` | Renders `commonAnnotations` |
| `oan-common.namespace` | Release namespace |

### Workload

| Helper | Purpose |
|---|---|
| `oan-common.image` | Full image ref from `image.registry`/`repository`/`tag`, falling back to `Chart.appVersion` then `latest`. `image.digest` pins by digest and wins over the tag. **Fails the render when `repository` is empty** |
| `oan-common.imagePullSecrets` | Renders the `imagePullSecrets` block from `image.pullSecrets` |
| `oan-common.resources` | Renders `resources`. **Fails the render when empty** — every OAN component must declare a resource contract |
| `oan-common.probes` | Renders every enabled probe block (startup, liveness, readiness) for a container spec |
| `oan-common.probeSpec` | Renders one probe. Takes `(dict "probe" <probe> "name" <name> "chart" .Chart.Name)` |
| `oan-common.podSecurityContext` | Pod-level security context, only when `podSecurityContext.enabled` |
| `oan-common.securityContext` | Container-level security context, only when `securityContext.enabled` |

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
| `oan-common.serviceAccount.name` | Service account name (generated, overridden, or `default` when disabled) |
| `oan-common.serviceAccount.enabled` | Emits `true` when a ServiceAccount should be created |

### Configuration and secrets

| Helper | Purpose |
|---|---|
| `oan-common.env` | Container `env` entries from `secretEnv` (env var name -> secret key) and `extraEnv` (raw passthrough) |
| `oan-common.envConfigMapName` | Name of the env ConfigMap (`<fullname>-env`) |
| `oan-common.envConfigMapData` | Renders `envConfig` into ConfigMap `data` entries |
| `oan-common.checksumAnnotation` | Checksum of `envConfig`, to roll pods when config changes |

### apiVersions

`oan-common.deployment.apiVersion` and `oan-common.ingress.apiVersion` keep those
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
