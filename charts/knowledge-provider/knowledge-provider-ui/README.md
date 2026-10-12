# knowledge-provider-ui

The [Bharat Vistaar Docs Pipeline](https://github.com/OpenAgriNet/knowledge-provider)
operator console — a React/Vite SPA, built and served as a static bundle by
nginx (`ui/Dockerfile.prod` in the app repo, `ui/nginx.prod.conf`).

## What it renders

| Resource | Notes |
|---|---|
| Deployment | Probes on `/health`, resources mandatory |
| Service | `ClusterIP` on 80 |
| ConfigMap (env) | Empty by default — see [Build-time configuration](#build-time-configuration) |
| ServiceAccount | `automountServiceAccountToken: false` — nginx calls no Kubernetes API |
| Ingress | Optional, off by default |

No Secret, no PersistentVolumeClaim, no dependency waiting — this is a
stateless static file server with no backing services to wait on. The browser
talks to `knowledge-provider-api` directly (or through an ingress/reverse
proxy in front of both), not this Pod.

## Build-time configuration

**This chart cannot configure the running application.** `VITE_API_BASE`,
`VITE_BASE`, `VITE_KEYCLOAK_URL`, `VITE_KEYCLOAK_REALM`,
`VITE_KEYCLOAK_CLIENT_ID`, `VITE_KEYCLOAK_IDP_HINT` and `VITE_AUTH_ENABLED` are
Vite build args — they get compiled into the static JS bundle when the image
is built (`docker build --build-arg VITE_API_BASE=... -f Dockerfile.prod`),
not read from the environment at container start. `envConfig`/`extraEnv` on
this chart reach the nginx process, which never looks at them.

Practical consequence: **one image per environment that needs a different API
base path, Keycloak realm, or auth toggle.** Point `image.tag` at the image
built for that environment; there is no values-only way to repoint an already
built image. This is a real limitation of the current CI/build setup, not
something to work around here — see the app repo's CI workflow
(`build-and-push.yml`) if you need to add a per-environment build matrix.

## Install

Install after `knowledge-provider-api` is reachable at the path this image's
`VITE_API_BASE` expects:

```bash
helm install knowledge-provider-ui charts/knowledge-provider/knowledge-provider-ui -n knowledge-provider \
  -f charts/knowledge-provider/knowledge-provider-ui/examples/knowledge-provider-ui.dev.yaml
```

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-ui charts/knowledge-provider/knowledge-provider-ui
```

Render-time guardrails:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)
