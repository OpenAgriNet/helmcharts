# keycloak

Keycloak for the OAN registry, on the [Sunbird RC Keycloak
image](https://github.com/Sunbird-RC/devops/tree/main/deploy-as-code). It issues
and validates the tokens the registry authorises requests with, and imports the
realm, clients and roles the registry expects.

This is the **legacy (WildFly/JBoss) Keycloak distribution**: it serves under
`/auth` and takes `DB_VENDOR`-style environment variables, not the modern `KC_*`
ones. Charts written for upstream Keycloak will not work with this image, which
is why this chart exists rather than depending on one.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | `strategy: Recreate` — one instance, and the realm import runs on startup. Init container waits for the database |
| Service | `ClusterIP` on 8080 |
| ConfigMap (env) | Extra non-secret config from `envConfig` |
| ConfigMap (realm) | The realm shipped in `files/`, mounted for `KEYCLOAK_IMPORT` |
| ServiceAccount | |
| PodDisruptionBudget | Optional, off by default |
| Ingress | Optional, off by default |
| Test Pod | `helm test` check on `/auth` and the imported realm |

Configuration matches `registry/docker-compose.yml` exactly — all ten Keycloak
environment variables, same names and same values.

## Install

```bash
helm dependency update charts/keycloak
helm install keycloak charts/keycloak \
  -n oan-registry -f charts/keycloak/examples/keycloak.dev.yaml
```

Requires a reachable PostgreSQL — see [`postgresql-cnpg`](../postgresql-cnpg) —
and two Secrets that this chart never creates: the database password and the
Keycloak admin password.

## The two-phase first install

**The registry cannot authenticate immediately after this chart installs.** The
realm export ships with the `admin-api` client secret masked (`**********`),
which is not a working credential. So:

1. Install this chart; it imports the realm on first start.
2. Reach the admin console:
   ```bash
   kubectl -n oan-registry port-forward svc/keycloak 8080:8080
   ```
   then open `http://localhost:8080/auth/admin`.
3. Realm `sunbird-rc` → Clients → `admin-api` → Credentials → **Regenerate
   Secret**.
4. Store that value where the registry chart's
   `keycloak.adminClientSecret` points.
5. Install the registry.

There is no way for a chart to shortcut this while the realm is imported from a
masked export. Making it one-phase means managing clients declaratively instead
— `keycloak-config-cli` or the Keycloak Operator — which is a larger change.

## Waiting for the database

Kubernetes has no equivalent of compose's `depends_on: condition:
service_healthy`, so an init container waits for the database before Keycloak
starts:

```yaml
waitFor:
  enabled: true
  database: true    # host and port come from `database`
```

Without it, a first install crashloops while PostgreSQL comes up — which recovers
on its own, but looks broken.

## Verifying an install

```bash
helm test keycloak -n oan-registry
```

Checks that `/auth` responds and, when `realmImport.enabled`, that the realm is
actually being served. That second check is the one that matters: the registry
validates tokens against that exact realm URL.

## Replicas and clustering

**This chart stays at one replica, and that is deliberate.** The legacy WildFly
distribution needs its Infinispan cache cluster configured — JGroups discovery
and session replication — before a second replica is safe. Without it, a token
issued by one replica is not recognised by the other. This chart does not
configure that, so `replicaCount: 1` and `strategy: Recreate` are the honest
settings.

The practical consequence: restarting Keycloak briefly interrupts token issuing.
The registry keeps serving requests whose tokens are already valid.

Getting past this means either configuring Infinispan clustering, or moving to a
current Keycloak with the Keycloak Operator — which would also remove the masked
client-secret problem below.

`podDisruptionBudget` is therefore off by default: over a single pod it blocks
node drains entirely.

## Per-environment values

| File | For |
|---|---|
| [`examples/keycloak.dev.yaml`](./examples/keycloak.dev.yaml) | Dev: superuser connection, hand-made Secrets, no ingress |
| [`examples/keycloak.prod.yaml`](./examples/keycloak.prod.yaml) | Production: dedicated `keycloak` role, JVM heap sized to the limit |

## Realm management

The chart ships `files/realm-export.json` — realm `sunbird-rc`, 8 clients,
including the `admin-api` and `registry-frontend` clients the registry uses — and
renders it into a ConfigMap mounted at `realmImport.mountPath`. The legacy image
imports it on startup via `KEYCLOAK_IMPORT`.

That means **`helm install` needs nothing prepared beforehand**, and a realm
change rolls the pod through the `checksum/realm` annotation.

| You want | Set |
|---|---|
| The shipped realm | nothing — this is the default |
| A realm managed outside the chart | `existingConfigMap: <name>` (must already exist; the chart renders none) |
| A different realm inline | `realmJson: <json>` |
| No import at all | `enabled: false` |

### Two things to know

**The shipped realm is a second copy.** It is byte-identical to the compose
stack's `registry/imports/realm-export.json`, which is deliberate — the cluster
should import the same realm that was verified locally. But nothing enforces
that: if the compose copy changes, this one has to be updated too, with a chart
version bump. That is the cost of the chart being self-contained.

**Editing the realm does not re-import it.** The legacy image imports a realm
only when it is *absent*. Changing the JSON rolls the pod, but Keycloak will
leave an already-imported realm alone — so changes to a live realm have to be
made in the Keycloak console, or by starting from a fresh database. This catches
people out, because the pod restart makes it look like something happened.

## Database## Database

The chart's `values.yaml` defaults match compose — Keycloak sharing the
registry's database — but **the example values point it at its own `keycloak`
database**, which is how the stack is meant to be deployed. Sunbird's own Helm
charts do the same. Keycloak's realm tables have no reason to sit beside registry
records, and separating them frees the registry to connect as a non-superuser
owner.

The `keycloak` database is created by
[`postgresql-migration`](../postgresql-migration)'s bootstrap target, which must
run before this chart. To reproduce compose exactly instead, set
`database.name: registry` and drop the migration step.

## Probes

These follow Sunbird's own chart rather than the compose healthcheck. Compose
curls the WildFly management port (9990); this chart does not expose 9990,
because nothing in-cluster needs it and the admin console is on 8080 under
`/auth/admin`. Readiness instead checks that Keycloak is really serving `/auth`.

First start imports the realm and migrates the schema, so `startupProbe` carries
the slow path (up to 5 minutes by default) and liveness stays tight.

## Image tag

Compose uses `latest`. A chart must not deploy a moving tag, so this defaults to
`v1.0.0` — the version Sunbird's own v2 deployment pairs with
`sunbird-rc-core:v2.0.0`. Set `image.digest` to pin exactly.

## Ingress and the issuer URL

`proxyAddressForwarding` is on, matching compose, so Keycloak will honour
`X-Forwarded-Host` / `X-Forwarded-Proto`. If you enable ingress:

- The controller must actually set those headers.
- The registry's `keycloak.url` must be whichever URL Keycloak puts in the `iss`
  claim. A mismatch rejects every authenticated request, and the failure reads
  like a permissions problem rather than a configuration one.

## Configuration

See [`values.yaml`](./values.yaml) for the full commented schema, and
[`examples/keycloak.dev.yaml`](./examples/keycloak.dev.yaml) for a per-environment
file.

## Versioning

Every change needs a `version` bump in `Chart.yaml` and an entry in
[`CHANGELOG.md`](./CHANGELOG.md) — see [`CONVENTIONS.md`](../../CONVENTIONS.md).
