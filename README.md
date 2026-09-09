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
| [`clickstack`](charts/clickstack) | application | Observability — ClickHouse, an OTel collector and the HyperDX UI. A verbatim copy of the official upstream chart, with no OAN changes yet. |

## How they fit together

```
charts/
├── common/          # library chart — shared helpers
├── template/        # starter chart — copy this to build a service chart
├── postgresql-cnpg/     # data store
├── postgresql-migration/# schema migrations (Flyway Job)
├── keycloak/            # auth for the registry
├── registry/            # the participant registry
├── discovery/           # the Beckn discover-and-publish service
├── adapter-service/     # the Beckn adapters — one release per role
└── clickstack/          # observability — vendored upstream, not yet OAN-shaped
```

Every chart depends on `common` via `file://../common`, except
`clickstack`: it is the official upstream chart committed unmodified, so it
carries neither the dependency nor the conventions. Its README lists what that
leaves to override.

## The registry stack

Three charts, deployed in this order — the ordering is not optional:

```bash
# 1. Database cluster. Creates BOTH databases: `registry` via bootstrap.initdb
#    and `keycloak` via a CNPG Database object, each owned by its own role.
helm install registry-db charts/postgresql-cnpg -n oan-registry -f charts/postgresql-cnpg/examples/registry-db.dev.yaml
# 2. Keycloak — imports the sunbird-rc realm it ships with, on first start
helm install keycloak    charts/keycloak        -n oan-registry -f charts/keycloak/examples/keycloak.dev.yaml
# MANUAL STEP: regenerate the admin-api client secret in the Keycloak console —
# the realm export ships it masked, so the registry cannot authenticate without this.
# 3. Registry
helm install registry    charts/registry        -n oan-registry -f charts/registry/examples/registry.dev.yaml
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
helm install discovery-db charts/postgresql-cnpg -n oan-discovery -f charts/postgresql-cnpg/examples/discovery-db.dev.yaml
# 2. The service
helm install discovery    charts/discovery       -n oan-discovery -f charts/discovery/examples/discovery.dev.yaml
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
