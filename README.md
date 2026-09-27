# helmcharts

Helm charts for deploying and managing OpenAgriNet (OAN) platform services.

## Charts

| Chart | Type | Purpose |
|---|---|---|
| [`common`](charts/common) | library | Shared template helpers — names, labels, image refs, probes, resources, service account, env config, dependency waits. Renders nothing; never installed directly. |
| [`template`](charts/template) | application | Complete, working starter chart built on `common`. Copy it to bootstrap a service chart. |
| [`postgresql-cnpg`](charts/postgresql-cnpg) | application | CloudNativePG-managed PostgreSQL cluster. One release per database. Requires the CNPG operator. |
| [`postgresql-migration`](charts/postgresql-migration) | application | Flyway migrations as a Job. Creates the per-service databases and applies versioned SQL. |
| [`keycloak`](charts/keycloak) | application | Auth for the registry, on the Sunbird RC Keycloak image. Imports the realm the registry expects. |
| [`registry`](charts/registry) | application | The OAN participant registry, on Sunbird RC core. Needs `postgresql-cnpg` and `keycloak`. |
| [`discovery`](charts/discovery) | application | The OAN Beckn discover-and-publish service. Needs `postgresql-cnpg` **with pgvector**. |
| [`adapter-service`](charts/adapter-service) | application | The OAN Beckn adapters. One chart, installed once per `role` — `provider`, `network` or `experience`. Needs `registry`. |
| [`kong`](charts/kong) | application | Kong — the cluster's API gateway and ingress controller, in one release. The official `kong` chart 3.2.0 committed whole and unmodified. Per-environment configuration lives in the infra-automation repository. |
| [`registry-seed`](charts/registry-seed) | application | Seeds the registry — adapter identities, upstreams, capability bindings and schemas — as a re-runnable Job. Reports the key osid each adapter needs. |
| [`cert-manager`](charts/cert-manager) | application | X.509 certificate management. The official `cert-manager` chart v1.21.2 committed whole and unmodified. Its CRDs are applied out of band — three exceed the annotation size limit. |
| [`cert-manager-issuers`](charts/cert-manager-issuers) | application | Let's Encrypt `ClusterIssuer`s. Separate from `cert-manager` because one release cannot register a CRD and create an instance of it. |
| [`clickstack`](charts/clickstack) | application | Observability — ClickHouse, an OTel collector and the HyperDX UI. A verbatim copy of the official upstream chart, with no OAN changes yet. |
| [`openbao`](charts/openbao) | application | The secret store. The official `openbao` chart 0.29.6 (OpenBao v2.6.3) committed whole and unmodified; OAN's settings are in `examples/openbao.dev.yaml` (one pod) and `examples/openbao.prod.yaml` (three-pod raft cluster). |
| [`openbao-cluster-secret-store`](charts/openbao-cluster-secret-store) | application | The `ClusterSecretStore` that lets External Secrets Operator read OpenBao, logging in with Kubernetes auth. |
| [`openbao-secrets`](charts/openbao-secrets) | application | One `ExternalSecret` per credential: pulls `oan/<env>/<name>` from OpenBao into a Secret and mirrors it, via Reflector, to the namespaces that read it. |

## How they fit together

```
charts/
├── common/          # library chart — shared helpers
├── template/        # starter chart — copy this to build a service chart
├── postgresql-cnpg/     # data store
├── postgresql-migration/# schema migrations (Flyway Job)
├── keycloak/            # auth for the registry
├── registry/            # the participant registry
├── registry-seed/       # seeds it, as a Job
├── kong/                # API gateway + ingress controller — vendored upstream
├── cert-manager/        # certificate management — vendored upstream
├── cert-manager-issuers/# Let's Encrypt ClusterIssuers for it
├── discovery/           # the Beckn discover-and-publish service
├── adapter-service/     # the Beckn adapters — one release per role
├── clickstack/          # observability — vendored upstream, not yet OAN-shaped
├── openbao/             # the secret store — vendored upstream
├── openbao-cluster-secret-store/ # how ESO reads it
└── openbao-secrets/     # what ESO reads from it
```

Every chart depends on `common` via `file://../common`, except `clickstack`,
`kong`, `cert-manager` and `openbao`: all four are official upstream charts committed
unmodified, so they carry neither the dependency nor the conventions. Each
README lists what that leaves to override. `cert-manager-issuers` also skips it,
for a different reason — it renders two custom resources and no workload, so
none of the library's helpers apply.

## The registry stack

Three charts, deployed in this order — the ordering is not optional:

```bash
# 1. Database cluster. Creates BOTH databases: `registry` via bootstrap.initdb
#    and `keycloak` via a CNPG Database object, each owned by its own role.
helm install postgres charts/postgresql-cnpg -n postgres -f charts/postgresql-cnpg/examples/postgres.dev.yaml
# 2. Secrets, before anything that reads them. One Secret per value, pulled from
#    OpenBao and mirrored where a second namespace needs it (see Secrets below):
#      infra-automation: ./scripts/manage-secrets.py generate --env dev
# 3. Keycloak — imports the sunbird-rc realm it ships with, on first start
helm install keycloak    charts/keycloak        -n keycloak -f charts/keycloak/examples/keycloak.dev.yaml
# 4. Registry
helm install registry    charts/registry        -n registry -f charts/registry/examples/registry.dev.yaml
# 5. Seed it: adapter identities, upstreams, bindings, schemas. Re-runnable.
#    Its log reports the key osid each adapter must be configured with - the
#    registry assigns those on write, so they cannot be known before this runs.
helm install registry-seed charts/registry-seed -n registry -f my-seed-values.yaml
```

`postgresql-migration` is deliberately not in that list: both databases come from
the cluster chart, and Sunbird RC and Keycloak each manage their own schema, so
there is nothing for Flyway to apply yet. It joins the flow when OAN adds schemas
of its own.

Their configuration is ported from the verified `registry/docker-compose.yml`
stack at exact environment-variable parity (32 for the registry, 10 for
Keycloak), so a cluster deploy reproduces what was tested locally. Each chart's
README documents where it deliberately deviates and why. Full walkthrough:
[`charts/registry/README.md`](charts/registry/README.md).

## The discovery service

Two charts, and one prerequisite that is easy to miss:

```bash
# 1. Its own database — pgvector on PostgreSQL 16, with the `vector` extension
#    created at bootstrap. Both are required: no stock CNPG operand image has
#    pgvector, and `vector` is not a trusted extension, so the owner the service
#    connects as cannot create it itself.
helm install discovery-db charts/postgresql-cnpg -n postgres -f charts/postgresql-cnpg/examples/discovery-db.dev.yaml
# 2. The service
helm install discovery    charts/discovery       -n discovery -f charts/discovery/examples/discovery.dev.yaml
```

It shares no database and no Keycloak with the registry stack, so the two are
independent installs. Full walkthrough, including how the DSN and the Beckn
specification are supplied:
[`charts/discovery/README.md`](charts/discovery/README.md).

Service charts depend on `common` and call its helpers through thin
chart-local wrappers. That keeps naming, labelling, probe, resource, and secret
conventions identical across every OAN chart, and means a convention change is
one edit in the library rather than one edit per chart.

## Quick start

Build a service chart from the template:

```bash
# 1. Copy the starter chart
cp -r charts/template charts/my-service

# 2. In charts/my-service/Chart.yaml set name: my-service
#    and appVersion to the image tag you deploy by default.
#    Keep the common dependency.

# 3. Rename the chart-local helpers to your service name. Change only the left
#    side of each define in templates/_helpers.tpl (template.* ->
#    my-service.*); the common.* include inside the body stays. This
#    renames the defines and the include calls together:
grep -rl 'template\.' charts/my-service | xargs sed -i '' 's/template\./my-service./g'
#    (sed -i '' is the macOS form; on Linux use sed -i)

# 4. Set image, ports, probe paths, resources and envConfig in
#    charts/my-service/values.yaml

# 5. Validate
./scripts/lint-charts.sh
helm template my-service charts/my-service
```

See [`charts/common/README.md`](charts/common/README.md) for the full
helper reference and
[`charts/template/README.md`](charts/template/README.md) for the
step-by-step adaptation guide.

## Validation

```bash
./scripts/lint-charts.sh
```

Runs `helm lint --strict` on every chart and `helm template` on every
application chart. CI runs the identical script
([`.github/workflows/helm-lint.yml`](.github/workflows/helm-lint.yml)) on pull
requests and on pushes to `main` and `development`.

Because charts depend on `common` through `file://../common` and the
packaged dependency is not committed, an edit to `common` only reaches a
consuming chart after `helm dependency update charts/<chart>` — or a run of the
lint script, which does it for you.

## Conventions

Chart naming, `version`/`appVersion` rules, changelog requirements, what every
service chart must declare, and how secrets are handled are documented in
[`CONVENTIONS.md`](CONVENTIONS.md).

Two of those rules are enforced at render time rather than at review time: a
chart with empty `resources` fails to render, and so does an enabled probe with
no handler or with more than one.

## Secrets

No secret value belongs in this repository, and **no chart here renders a
Secret**. Charts reference Secrets by name; creating them is deliberately left
outside the charts, so that decision is made once rather than per chart. See
[`CONVENTIONS.md`](CONVENTIONS.md#secrets).

### Bringing up the secret store

OpenBao holds every value; External Secrets Operator copies them into
Kubernetes Secrets; Reflector mirrors those into the namespaces that read them.

```bash
# 1. The unseal key. OpenBao's "static" seal reads it on every start, so a
#    restarted pod unseals itself -- no AWS KMS, no manual unseal. Keep a copy
#    of unseal.key OFF the cluster: without it the data cannot be decrypted.
openssl rand -out unseal.key 32
kubectl create namespace openbao
kubectl -n openbao create secret generic openbao-unseal-key --from-file=unseal.key=unseal.key

# 2. OpenBao. openbao.dev.yaml is one pod; production uses openbao.prod.yaml,
#    three pods in a raft cluster on three nodes -- start prod that way rather
#    than converting a running single node later.
helm install openbao charts/openbao -n openbao -f charts/openbao/examples/openbao.dev.yaml

# 3. One-time setup: init (writes the root token and recovery keys to the
#    file given -- move it to the password manager), KV v2 at secret/,
#    Kubernetes auth, a read-only policy for oan/<env>/*, the role ESO uses,
#    and the role the backup CronJob uses.
./scripts/openbao-configure.sh --env dev --init-out ~/openbao-dev-init.json

# 4. The operator, the mirror, and the store they read
helm install external-secrets charts/external-secrets -n external-secrets --create-namespace
helm install reflector        charts/reflector        -n reflector        --create-namespace
helm install openbao-store    charts/openbao-cluster-secret-store

# 5. Write the values, then declare which ones reach the cluster
kubectl -n openbao exec -it openbao-0 -- bao kv put secret/oan/dev/registry-db username=... password=...
helm install openbao-secrets charts/openbao-secrets -n external-secrets
```

### Checking that it works

| Check | Command | Expect |
|---|---|---|
| Chart renders | `helm lint --strict charts/openbao -f charts/openbao/examples/openbao.dev.yaml` | `0 chart(s) failed` |
| Unsealed | `kubectl -n openbao exec openbao-0 -- bao status` | `Seal Type static`, `Sealed false` |
| Unseals on restart | `kubectl -n openbao delete pod openbao-0`, then `bao status` again | `Sealed false`, with nobody unsealing it |
| Audit log on | `kubectl -n openbao exec openbao-0 -- bao audit list` (with `BAO_TOKEN`) | `file/` and `stdout/` |
| ESO can log in | `kubectl get clustersecretstore openbao` | `READY True` |
| Values arrive | `kubectl -n external-secrets get externalsecret` | every row `SecretSynced` |
| Mirrored | `kubectl -n registry get secret registry-db` | exists |
| Policy is tight | an `ExternalSecret` for `oan/prod/...` on the dev cluster | `SecretSyncedError`, "permission denied" |
| Store is fenced | an `ExternalSecret` on the `openbao` store in any namespace but `external-secrets` | `SecretSyncedError`, "not allowed from namespace" |
| Network is fenced | `curl http://openbao.openbao.svc:8200/v1/sys/health` from a pod in `default` | times out |
| Backups work | `kubectl -n openbao create job snap-now --from=cronjob/openbao-snapshot` | a new `bao_<date>.snapshot` in the bucket |
| HA formed (prod) | `bao operator raft list-peers` (with `BAO_TOKEN`) | three voters, one `leader` |

A config or image change reaches OpenBao only when its pod is deleted: the
chart's update strategy is `OnDelete`, and Argo CD shows the app Synced either
way. In prod, delete the standbys first and the active pod
(`openbao-active=true`) last, one at a time.

### Backups and restore

The snapshot CronJob (`snapshotAgent`, off in dev until a bucket exists -- see
`openbao.dev.yaml`) uploads a raft snapshot of the whole store every hour. A
snapshot can only be opened with the unseal key it was taken under, so keep the
key with the snapshots, off the cluster. To restore, into a fresh OpenBao that
mounts that same key:

```bash
bao operator init                                   # a fresh store needs initialising first
bao operator raft snapshot restore -force bao_<date>.snapshot   # with that init's root token
# From here the ORIGINAL store's root token and data are back; the fresh one's are gone.
```

### Moving a cluster from AWS Secrets Manager

Copy the values -- never regenerate them: Postgres role passwords, the
admin-api client secret Keycloak stored at import, and the adapter keys the
registry holds all depend on the current ones. Then switch the existing release
in place, and diff before against after.

```bash
./scripts/secrets-snapshot.sh > before.txt              # hashes only, no values
BAO_TOKEN=<root token> ./scripts/asm-to-openbao.sh --env dev --dry-run
BAO_TOKEN=<root token> ./scripts/asm-to-openbao.sh --env dev
helm install openbao-store charts/openbao-cluster-secret-store
helm upgrade asm-secrets charts/openbao-secrets -n external-secrets   # the EXISTING release
kubectl -n external-secrets get externalsecret           # every row: openbao, SecretSynced
./scripts/secrets-snapshot.sh > after.txt
diff before.txt after.txt                                # must be empty
```

`asm-secrets` here is the Helm release already running in the cluster; its
chart, and `asm-cluster-secret-store`, have been removed from this repository.
The release keeps its name after the upgrade, and `helm rollback` works from
its stored history, so neither needs the old chart.

Upgrade the release, do not uninstall it: its ExternalSecrets own their
Secrets, so an uninstall deletes them until the new chart recreates them. If the
diff is not empty, `helm rollback asm-secrets -n external-secrets` puts it back
on Secrets Manager, which is untouched by all of this.

A rotation is `bao kv put` with the new value; it reaches the cluster within
`refreshInterval` (1h by default), and pods pick it up on their next restart.
