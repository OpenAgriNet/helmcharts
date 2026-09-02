# registry

The OAN participant registry, on [Sunbird RC
core](https://github.com/Sunbird-RC/sunbird-rc-core). It holds participant
records and the key material that inbound signed requests are verified against.

Configuration is ported from the verified `registry/docker-compose.yml` stack —
all 32 environment variables, at parity. This deployment is **participant records
and lookup only**: every optional Sunbird RC subsystem (credentials, DIDs,
certificates, encryption, file storage, notifications, webhooks, async) is off,
exactly as in compose.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | Probes on `/health`, resources mandatory, init containers wait for dependencies |
| Service | `ClusterIP` on 8081 |
| ConfigMap (env) | The non-secret flags from `envConfig` |
| ConfigMap (schemas) | Entity definitions, mounted read-only |
| ServiceAccount | |
| HorizontalPodAutoscaler | Optional, off by default |
| PodDisruptionBudget | Optional, off by default |
| Ingress | Optional, off by default |
| Test Pod | `helm test` check against `/health` |

## Dependencies

Two things must already be running:

1. **PostgreSQL** → `database.host`. See [`postgresql-cnpg`](../postgresql-cnpg),
   which creates the database and the owner role this chart connects as.
2. **Keycloak** → `keycloak.url`. See
   [`keycloak`](../keycloak).

## Image

`image.repository` is **empty by default, on purpose.** The image OAN will
actually deploy is not decided yet — upstream `ghcr.io`, a mirror in our own
registry, or an OAN-built image — and a default here would read as a decision
that has not been made.

The render **fails** while it is empty:

```
registry: image.repository is required - set image.registry/repository/tag for this environment
```

That is deliberate. An empty repository would otherwise render
`image: ghcr.io/:v2.0.0`, which Helm and the API server both accept, and which
only fails later as an `ImagePullBackOff`.

| Values file | Image |
|---|---|
| `examples/registry.dev.yaml` | `ghcr.io/sunbird-rc/sunbird-rc-core:v2.0.0` — what the compose stack runs, so dev deploys what was verified locally |
| `examples/registry.prod.yaml` | Empty, with a TODO. Not installable until filled in |

When you do fill in production, prefer `image.digest` over `image.tag`: a tag can
be repointed at different bits, a digest cannot, which is what makes a rollback
land on the same image it did before.

## Waiting for dependencies

Kubernetes has no equivalent of compose's `depends_on: condition:
service_healthy`, so the chart adds init containers:

```yaml
waitFor:
  enabled: true
  database: true    # TCP check, host/port from `database`
  keycloak: true    # HTTP check on <keycloak.url>/realms/<realm>
```

Both are derived from settings you already set, so there is nothing to keep in
sync.

The Keycloak check is deliberately **HTTP against the realm endpoint**, not TCP
against the port. Keycloak imports the realm during its first start, so the port
opens well before the realm exists — a TCP check would pass too early and the
registry would come up against a Keycloak that cannot yet issue it a usable
token.

They give up after `waitFor.timeoutSeconds` (default 300) so a genuinely missing
dependency shows as a failed init container in `kubectl describe`, rather than an
endless wait.

## Verifying an install

```bash
helm test registry -n oan-registry
```

Runs a Pod that checks `/health`. It deliberately does not test an authenticated
endpoint: that needs a real token, and a failure there would be ambiguous between
"the registry is broken" and "the Keycloak wiring is wrong".

## Scaling

The registry is stateless, so `autoscaling.enabled` gives it an HPA. Two things
to know:

- Every replica opens its own connection pool, so scaling out multiplies
  connections against PostgreSQL. Check the cluster's `max_connections` before
  raising `maxReplicas` much.
- When autoscaling is on, the Deployment omits `replicas` entirely, so
  `replicaCount` is ignored. Otherwise Helm and the HPA fight on every reconcile.

`podDisruptionBudget.enabled` keeps a replica serving through node drains. It
**fails the render** if enabled with `replicaCount: 1`, because a PDB of
`minAvailable: 1` over a single pod blocks drains completely — set
`allowSingleReplica: true` if you want that anyway.

## Per-environment values

| File | For |
|---|---|
| [`examples/registry.dev.yaml`](./examples/registry.dev.yaml) | Dev: superuser connection, hand-made Secrets, no ingress |
| [`examples/registry.prod.yaml`](./examples/registry.prod.yaml) | Production: owner connection, 2 replicas, PDB, anti-affinity, security contexts on |

Everything that differs between environments lives in these files, not in
templates.

## Install order

The full stack, in the only order that works:

```bash
# 1. Database cluster. Creates BOTH databases - `registry` via bootstrap.initdb,
#    `keycloak` via a CNPG Database object - each owned by its own role.
helm install registry-db charts/postgresql-cnpg \
  -n oan-registry -f charts/postgresql-cnpg/examples/registry-db.dev.yaml

# 2. Keycloak — imports the sunbird-rc realm on first start
helm install keycloak charts/keycloak \
  -n oan-registry -f charts/keycloak/examples/keycloak.dev.yaml

# 3. MANUAL: regenerate the admin-api client secret.
#    The realm export ships it masked ("**********"), so it does not work as-is.
#    kubectl -n oan-registry port-forward svc/keycloak 8080:8080
#    http://localhost:8080/auth/admin -> realm sunbird-rc -> Clients
#      -> admin-api -> Credentials -> Regenerate Secret
#    kubectl -n oan-registry create secret generic registry-keycloak \
#      --from-literal=keycloakAdminClientSecret='<regenerated>' \
#      --from-literal=registryDefaultUserPassword='<password>'

# 4. Registry
helm install registry charts/registry \
  -n oan-registry -f charts/registry/examples/registry.dev.yaml
```

Step 3 is unavoidable while the realm is imported from a masked export — see
[`keycloak`](../keycloak#the-two-phase-first-install).

## Entity schemas

The registry treats every `.json` under its schema directory as an entity it
serves APIs for. The chart ships `files/schemas/Participant.json` and mounts the
rendered ConfigMap read-only at
`/home/sunbirdrc/config/public/_schemas`. A `checksum/schemas` annotation rolls
the pod when a schema changes.

| You want | Set |
|---|---|
| The shipped schemas | nothing — this is the default |
| Schemas managed outside the chart | `schemas.existingConfigMap: <name>` |
| Extra or overriding schemas | `schemas.inline: {Name.json: '<json>'}` |

The render fails if no schema matches, since a registry with no entity
definitions serves nothing.

> **The participant schema is not settled.** Per engineering-tracker #33, it was
> built and verified against the local stack but has no design issue of its own
> (#43 was closed as a duplicate of #69, which covers a different schema).
> Expect it to change, and a schema change is a chart release.

## Authentication

`authenticationEnabled: true` — role checks on `Participant` only apply while
this is true. Turning it off makes every endpoint unauthenticated; it is a
local-debugging switch, not a deployment option.

Three settings must agree with Keycloak, and getting any of them wrong produces
401/403 responses that read like a permissions bug rather than a configuration
one:

| Setting | Must be |
|---|---|
| `keycloak.url` | Exactly the issuer Keycloak puts in the `iss` claim, including `/auth` |
| `keycloak.realm` | A realm that is actually imported (`sunbird-rc`) |
| `keycloak.adminClientSecret` | The **regenerated** `admin-api` secret, never the masked one from the export |

`OAUTH2_RESOURCES_0_URI` is derived as `<keycloak.url>/realms/<realm>`, so it
cannot drift from `keycloak.url`.

## Database

Defaults match compose: the registry connects to database `registry` as the
`postgres` superuser, sharing that database with Keycloak.

**CNPG disables superuser access by default**, so reproducing the compose
arrangement requires `enableSuperuserAccess: true` on the database chart — which
the example values set, and which makes CNPG generate
`Secret/<cluster>-superuser` for the password.

Once you are past reproducing compose, the better arrangement is: leave
superuser access off, connect the registry as the cluster's bootstrap owner
(`database.user: registry`, password from `Secret/<cluster>-app`), and give
Keycloak its own database and role. Then no workload holds superuser rights.

## Secrets

Three values, none of which this chart ever renders:

| Value | Setting |
|---|---|
| Database password | `database.passwordSecret` |
| `admin-api` client secret | `keycloak.adminClientSecret` |
| Default password for Keycloak users the registry creates | `defaultUserPasswordSecret` |

This chart renders none of them — it only references Secrets, which are created
by whatever manages secrets in that environment. For dev that is a
`kubectl create secret`; the example values show the exact command.

## Probes

`/health`, matching the compose healthcheck. Sunbird's own chart probes
`/api/docs/swagger.json`, which additionally requires swagger to be enabled;
`/health` is the narrower check. The registry migrates its schema on first start,
so `startupProbe` carries the slow path.

## Configuration

See [`values.yaml`](./values.yaml) for the full commented schema and
[`examples/registry.dev.yaml`](./examples/registry.dev.yaml) for a
per-environment file.

The `*_enabled: "false"` flags in `envConfig` are deliberate. Each one that is
turned on pulls in another Sunbird RC service that is not deployed — the registry
will start, then fail at the first request that needs it.

## Versioning

Every change needs a `version` bump in `Chart.yaml` and an entry in
[`CHANGELOG.md`](./CHANGELOG.md) — see [`CONVENTIONS.md`](../../CONVENTIONS.md).
