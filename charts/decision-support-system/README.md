# decision-support-system

The DSS [reasoning
runtime](https://github.com/OpenAgriNet/decision-support-system) — interprets
one farmer turn (intent, moderation, provider discovery, planning,
composition) and streams back a curated answer, citing whichever OAN provider
answered. Stateless: no datastore of its own. State lives in the LLM
providers it calls and in the OAN network it discovers against, not here.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | Probes on `/openapi.json` — see [Health checks](#health-checks), the one real gap here |
| Service | `ClusterIP` on 8077 |
| ConfigMap (env) | Model bindings, network wiring, schema-pack/evidence paths derived from structured values; free-form overrides via `envConfig` |
| ServiceAccount | `automountServiceAccountToken: false` — the service calls no Kubernetes API |
| HorizontalPodAutoscaler | Optional, off by default. Safe to enable — see [Scaling](#scaling) |
| PodDisruptionBudget | Optional, off by default |
| Ingress | Optional, off by default — see [Not exposed by default](#not-exposed-by-default) |
| Test Pod | `helm test` check that `/openapi.json` answers |

No Secret is rendered. `AZURE_OPENAI_API_KEY` / `OPENAI_API_KEY` /
`OTEL_EXPORTER_OTLP_HEADERS` / `GITHUB_TOKEN` are all referenced by name from
Secrets created outside this chart.

## Install

```bash
kubectl create secret generic dss-azure-openai \
  -n dss --from-literal=api-key=<the deployment key>
kubectl create secret docker-registry ghcr -n dss \
  --docker-server=ghcr.io --docker-username=<user> \
  --docker-password=<a PAT with read:packages>

helm install decision-support-system charts/decision-support-system -n dss \
  -f charts/decision-support-system/examples/decision-support-system.dev.yaml

helm test decision-support-system -n dss
```

## Configuration

The chart structures the values most likely to change per deployment; the
rest of `src/dss/config/settings.py` is available through `envConfig`. See
[decision-support-system's docs/RUNNING.md](https://github.com/OpenAgriNet/decision-support-system/blob/main/docs/RUNNING.md#knobs)
for the full list.

### Models (ADR-0004)

Four agents, each its own model string, set through `models.{intent,moderation,planner,composer}`:

- `openai:<model>` — needs `openai.apiKeySecret` (→ `OPENAI_API_KEY`), optionally `openai.baseUrl`
- `azure:<deployment id>` — needs `azureOpenai.endpoint` + `azureOpenai.apiKeySecret` (→ `AZURE_OPENAI_ENDPOINT`/`AZURE_OPENAI_API_KEY`). The part after `azure:` is the **deployment id**, not a model name.

One agent can be on Azure while another is on OpenAI. The chart fails the
render if any `models.*` value starts `azure:` and `azureOpenai.*` is not
fully set — the app itself only catches this at process boot
(`entrypoint/composition.py::_resolve_model`), one agent at a time.

### The provider network is all-or-nothing

`network.discoveryBaseUrl` and `network.invocationBaseUrl` gate the real
discover → plan → compose path (`Settings.network_enabled`). Leave both unset
and every turn still answers — intent and moderation run for real, discovery
finds nobody, and the turn completes with `outcome.status: "no_match"`. That
is the app's own designed degrade path, not a wiring fault, and it is what
lets this chart be installed before an OAN network exists to point at.

Setting only one of the two is a real footgun the app does not catch at
boot — `network_enabled` is false either way, so the turn still completes as
`no_match`, but now it looks like discovery is broken rather than
unconfigured. This chart fails the render instead of reproducing that
silence.

### Schema packs

The packs describing what a provider can answer live in a separate repo
(`OpenAgriNet/network-specs`) and ship in neither this chart nor the app
image. With the network wired, the app fetches them itself over the GitHub
API on boot *if* the directory it is pointed at is empty
(`_ensure_schema_packs` in `composition.py`), and refuses to serve if that
fetch also finds nothing.

This chart mounts `schemaPacks.mountPath` as an `emptyDir`, so every pod
fetches independently on its own empty volume — fine at low replica counts
and infrequent restarts, but each fetch counts against GitHub's unauthenticated
60-requests/hour cap. Set `schemaPacks.githubTokenSecret` once that becomes a
problem (raises the cap to 5000/hour). `schemaPacks.ref` **must** be set to a
ref that actually carries packs — the app's own default (`main`) carries only
a README and fails the boot naming that ref, by design.

### Evidence is intentionally not persistent

Every turn appends to two JSONL files under `evidence.mountPath` (also an
`emptyDir`): `telemetry.jsonl` (stage/outcome, no user content) and
`turns.jsonl` (the question and answer, restricted retention). Nothing in the
app reads these files back — they exist because the real evidence API this is
meant to post to
([docs/RUNNING.md, "The external endpoint"](https://github.com/OpenAgriNet/decision-support-system/blob/main/docs/RUNNING.md#the-external-endpoint))
does not exist yet. A PVC here would imply a durability guarantee the app was
never designed to have; losing them on pod restart is the correct behaviour
today, not a gap this chart should paper over. Revisit once
`DSS_EVIDENCE_URL` is actually wired upstream.

### Tracing

`tracing.otlpEndpoint` is the standard `OTEL_EXPORTER_OTLP_ENDPOINT` — a
Collector or Langfuse's OTLP ingest, neither of which this chart deploys.
`decision-support-system/docker-compose.yml` carries an `otel-collector`
behind a `--profiles: tracing` gate for local dev only; a real deployment
points at whatever Collector or Langfuse already runs in the cluster instead.
Leave `tracing.otlpEndpoint` unset and nothing is instrumented, which is the
correct default — an unreachable endpoint otherwise produces a
`Transient error ... Connection refused` warning per span.

Never set `DSS_TRACE_INCLUDE_MESSAGE_CONTENT` through `envConfig` in a real
deployment. It puts the farmer's query and the composed answer into every
span verbatim; `DSS_ARCHITECTURE.md` §6.1 forbids exactly this.

## Health checks

There is no dedicated `/health` or `/readyz` route — only `POST /v1/turns` is
registered (`adapters/http/v1/router.py`). `/openapi.json` is what
`docker-compose.yml`'s own healthcheck uses: FastAPI serves it
unconditionally once the ASGI app is constructed, with no dependency check
behind it.

That makes it a genuine **liveness** signal — the process is up and serving
HTTP — but **not a readiness** one: `Settings.ready` defaults to `true` and,
when flipped to `false`, only makes `POST /v1/turns` itself return `503`
(`router.py`). No probe route observes that flag, so a Kubernetes
`readinessProbe` cannot use it to pull a pod out of rotation before a turn is
attempted. This is a genuine gap in the upstream app, not something worked
around here — flagging it rather than routing the probe at something that
would silently always pass.

## Scaling

The app's own design (`docs/dss-design-v2.md`: "any replica can serve the
rest [of a resumed stream]") is stateless and horizontally scalable — no
cross-replica coordination, no shared cache to invalidate. `autoscaling.enabled`
is safe to turn on; `replicaCount` is ignored once it is.

## Not exposed by default

`ingress.enabled` defaults to `false`. The app is explicit about this in its
own source (`entrypoint/app.py`): "No auth and no CORS by design — only this
deployment's own channel services reach this port." Put a channel/gateway
service in front rather than exposing this chart's Service directly, unless
that gateway service *is* what you'd enable this Ingress for.

## Known gap: the Dockerfile runs as root

`decision-support-system/Dockerfile` is `python:3.13-slim` with no `USER`
directive, so the container runs as root today. `securityContext` /
`podSecurityContext` are disabled by default in this chart to reflect that
honestly, rather than declaring `runAsNonRoot: true` against an image that
does not support it. Once the Dockerfile gains a non-root `USER`, flip both
`enabled: true` and verify the schema-pack/evidence `emptyDir` mounts are
writable by that UID.
