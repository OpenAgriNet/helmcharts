# knowledge-provider-keycloak

Keycloak for the [Bharat Vistaar Docs Pipeline](https://github.com/OpenAgriNet/knowledge-provider)
— a **custom, modern (Quarkus-based) Keycloak image**
(`knowledge-provider/keycloak/Dockerfile`, built from
`quay.io/keycloak/keycloak:26.1.4`) with the
[email-OTP 2FA provider](https://github.com/mesutpiskin/keycloak-2fa-email-authenticator)
jar and a realm-import JSON both **baked into the image at build time**. This
chart sets environment/DB/hostname/SMTP configuration and runs
`start --import-realm`; it does not mount a realm ConfigMap or a provider jar
volume, because the image already carries both.

## Not the same as this repo's `keycloak` chart

This repository already has a [`keycloak`](../../keycloak) chart, but it wraps a
**different, legacy Keycloak distribution** — the Sunbird RC WildFly/JBoss
fork, which serves under `/auth` with `DB_VENDOR`-style env vars and imports
realms into `/opt/jboss/keycloak/imports`. That chart's own README says as
much: "charts written for upstream Keycloak will not work with this image."

`knowledge-provider`'s image is the inverse case — modern Quarkus Keycloak
(`KC_*` env vars, providers under `/opt/keycloak/providers`, health endpoints
on a separate management port) — so it needs its own chart rather than
reusing either the existing `keycloak` chart or its wiring conventions.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | `start --import-realm`; two container ports (`http` 8080, `health` 9000 — the Quarkus management interface); resources mandatory |
| Service | `ClusterIP` on 8080 (main HTTP port only — health checks hit the Pod directly, not through the Service) |
| ConfigMap (env) | Empty by default — nearly everything is derived from structured values, see [Configuration](#configuration) |
| ServiceAccount | `automountServiceAccountToken: false` |
| PersistentVolumeClaim | Optional, off by default — see [Persistence](#persistence) |
| Ingress | Optional, off by default |

No Secret is rendered — see [Secrets](#secrets).

## Install

```bash
# 1. Its database
helm install knowledge-provider-keycloak-db charts/postgresql-cnpg -n knowledge-provider \
  -f charts/postgresql-cnpg/examples/knowledge-provider-keycloak-db.dev.yaml

kubectl -n knowledge-provider create secret generic knowledge-provider-keycloak-admin \
  --from-literal=password=<admin-password>

# 2. Keycloak
helm install knowledge-provider-keycloak charts/knowledge-provider/knowledge-provider-keycloak -n knowledge-provider \
  -f charts/knowledge-provider/knowledge-provider-keycloak/examples/knowledge-provider-keycloak.dev.yaml
```

Starting before the database is up is not fatal — the `waitFor` init
container blocks on `database.host:database.port` until it is reachable, or
fails the pod after `waitFor.timeoutSeconds` with the reason named.

## Realm import only happens once

`--import-realm` imports the realm baked into the image **only when no realm
of that name exists yet** in the database. Shipping a new image with an
updated realm-import JSON does **not** update an already-running deployment —
that is a Keycloak Admin API/CLI operation against the live realm, not
something this chart (or a redeploy) does for you.

## Configuration

Nearly everything this image needs is derived from structured values, so it
cannot drift out of sync:

| Variable | Comes from |
|---|---|
| `KC_DB`, `KC_DB_URL` | fixed to `postgres`, assembled from `database.host/port/name/sslmode` |
| `KC_DB_USERNAME`, `KC_DB_PASSWORD` | `database.user`, `database.passwordSecret` |
| `KC_BOOTSTRAP_ADMIN_USERNAME`, `KC_BOOTSTRAP_ADMIN_PASSWORD` | `admin.username`, `admin.passwordSecret` |
| `KC_HOSTNAME` | `hostname` — **required** |
| `KC_HOSTNAME_STRICT`, `KC_HTTP_ENABLED`, `KC_PROXY_HEADERS`, `KC_HEALTH_ENABLED` | fixed (`false`, `true`, `xforwarded`, `true`) |
| `KC_HTTP_RELATIVE_PATH` | `httpRelativePath` (default `/auth`) |
| `KC_SPI_EMAIL_SENDER_*` | `smtp.*` — skipped entirely when `smtp.host` is empty (e.g. when SMTP is already set in the baked-in realm JSON) |

Use `envConfig` only for a `KC_SPI_*`/`KC_*` setting this chart does not
already model.

## Secrets

None are rendered. Defaults name the Secrets this chart expects:

| Secret (default name) | Key | Notes |
|---|---|---|
| `knowledge-provider-keycloak-db-app` | `password` | Matches the CNPG-generated app Secret when the `knowledge-provider-keycloak-db` release uses `bootstrap.owner: keycloak` |
| `knowledge-provider-keycloak-admin` | `password` | Console login for the bootstrap admin, `admin.username` |
| `knowledge-provider-keycloak-smtp` | `password` | Only read when `smtp.host` is set |

## Persistence

`/opt/keycloak/data` is optional and off by default. All durable state
(realm, users, sessions, credentials) lives in Postgres via `KC_DB` — this
volume only holds local caches, so losing it on a pod restart costs nothing
correctness-wise. Enable it only if you specifically want those caches to
survive a restart.

## Validation

```bash
../../../scripts/lint-charts.sh
helm template knowledge-provider-keycloak charts/knowledge-provider/knowledge-provider-keycloak \
  -f charts/knowledge-provider/knowledge-provider-keycloak/ci/lint-values.yaml
```

Render-time guardrails, all of which name the value and the reason:

- `image.repository` empty (`common`)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)
- `database.host` empty (`waitFor`, and again in the env helper)
- `database.passwordSecret.name` / `admin.passwordSecret.name` empty
- `hostname` empty
