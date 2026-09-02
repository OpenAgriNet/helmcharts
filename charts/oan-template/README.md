# oan-template

The **reference / starter** Helm chart for OpenAgriNet (OAN) services.

`oan-template` is a complete, working example of an OAN service chart built on
the [`oan-common`](../oan-common) library chart. It is meant to be **copied and
adapted** — not installed as-is.

## What it renders

| Resource | File | Notes |
|---|---|---|
| Deployment | `templates/deployment.yaml` | Probes and resources always present; security contexts gated on `enabled` |
| Service | `templates/service.yaml` | `ClusterIP` by default |
| ServiceAccount | `templates/serviceaccount.yaml` | Created when `serviceAccount.enabled` |
| ConfigMap | `templates/configmap.yaml` | Built from `envConfig`; checksum rolls pods on change |
| Ingress | `templates/ingress.yaml` | Optional, off by default |

Names, labels, image refs, probes, resources, and secret wiring all come from
`oan-common` helpers, so every chart derived from this template stays consistent.

## How it depends on oan-common

`Chart.yaml` declares:

```yaml
dependencies:
  - name: oan-common
    version: "0.1.x"
    repository: "file://../oan-common"
```

The chart-local helpers in `templates/_helpers.tpl` are thin wrappers that
delegate to the library (`oan-template.fullname` → `oan-common.fullname`).

## Create your own chart from this template

1. Copy the directory and rename it:

   ```bash
   cp -r charts/oan-template charts/oan-my-service
   ```

2. In `charts/oan-my-service/Chart.yaml`, set `name: oan-my-service` and
   `appVersion` to the image tag you deploy by default. Keep the `oan-common`
   dependency.

3. Rename the chart-local helpers. Change only the **left side** of each
   `define` in `templates/_helpers.tpl` — the `oan-common.*` include inside the
   body stays, since that is the shared library you delegate to. Then update the
   matching `include "oan-template..."` calls in the template YAML. Both at once,
   from the repo root:

   ```bash
   grep -rl 'oan-template\.' charts/oan-my-service | xargs sed -i '' 's/oan-template\./oan-my-service./g'
   ```

   (`sed -i ''` is the macOS form; on Linux use `sed -i`.)

4. Set the real values in `values.yaml`:
   - `image.registry` / `image.repository` / `image.tag`
   - `service.port` / `service.targetPort` — your container's listen port
   - `livenessProbe.httpGet.path` / `readinessProbe.httpGet.path` — your health endpoint
   - `resources` — right-sized for the workload
   - `envConfig` — non-secret configuration only

5. Validate:

   ```bash
   ./scripts/lint-charts.sh
   helm template oan-my-service charts/oan-my-service
   ```

## Try the template directly

```bash
helm dependency update charts/oan-template
helm template demo charts/oan-template \
  --set image.repository=nginx --set image.tag=1.27
```

## Configuration

See [`values.yaml`](./values.yaml) for the full, commented schema.

Two behaviours worth knowing before you override:

- **`resources` is mandatory.** Emptying it fails the render by design.
- **Switching a probe handler needs the default nulled out.** Helm merges maps,
  so replacing the default `httpGet` probe with `tcpSocket` leaves both handlers
  in the merged value. The render fails with an explanatory error; null the one
  you are replacing:

  ```bash
  --set livenessProbe.tcpSocket.port=http --set livenessProbe.httpGet=null
  ```

## Secrets

Never put secret values in `envConfig` or in any committed values file. This
chart renders no Secrets at all — it only *references* them, so they are created
by whatever manages secrets in that environment.

Two ways to consume one:

```yaml
# whole Secret injected as env vars
envFromSecrets:
  - my-service-db

# a single key mapped to a specific variable name, for when they differ
secretEnv:
  DB_PASSWORD:
    name: my-service-db
    key: password
```

## Versioning

Every change needs a `version` bump in `Chart.yaml` and an entry in
[`CHANGELOG.md`](./CHANGELOG.md) — see [`CONVENTIONS.md`](../../CONVENTIONS.md).
