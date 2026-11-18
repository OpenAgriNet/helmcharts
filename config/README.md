# config

Everything one environment runs, deployed by Argo CD. This directory is itself a
Helm chart: it renders one Argo CD Application per service.

```
config/
  Chart.yaml, values.yaml, templates/   the chart
  example/                              a minimal sample of the four files (committed)
  <env>/argocd.yaml                     Argo CD settings and which services run
  <env>/global-values.yaml              values many services share, each a YAML anchor
  <env>/service-values.yaml             every service in install order, with its values
  <env>/resource-values.yaml            each service's resources (requests/limits)
```

Real environments are **local**: `config/*/` is gitignored except `example/`.
`example/` shows the shape of each file with a few services; a real
environment lists every service it runs. Start one from an existing
environment folder, or from the sample:

```bash
cp -r config/example config/dev
# then set environment and domain (global-values.yaml), add the services
# (service-values.yaml, resource-values.yaml) and the applications: list and
# argocd.targetRevision (argocd.yaml)
```

## Values

A shared value is an anchor in `global-values.yaml`:

```yaml
global:
  domain: &domain oan.example.org
```

`service-values.yaml` reads it as an alias where it is the whole value, and as
`${name}` inside a longer string, where an alias cannot reach:

```yaml
    participantId: *domain
    baseUrl: https://${domain}
```

Change the domain once in `global-values.yaml` and every service follows.

- An alias keeps its type (a map stays a map).
- An unknown `${name}` fails the render. OTel's own `${env:VAR}` has a colon and
  is left as it is.
- A global left empty (a key not issued yet) fails the render until it is set,
  in `global-values.yaml` or under `global:` in `argocd.yaml`.
- `resource-values.yaml` holds every `resources:` block at the same key path as in
  the service's values. One inside a list (a sidecar container) stays in
  `service-values.yaml`: a list item cannot be merged back by key.

A service entry takes `chart` and `namespace`, and optionally `repository` and
`version` (a chart from a Helm repository), `releaseName`, `targetRevision`,
`syncWave`, `serverSideApply` and `ignoreDifferences`.

## Turning a service on or off

Every environment lists every service; which ones run is the `applications:`
list in `config/<env>/argocd.yaml`:

```yaml
applications:
  metrics-server: false
  grafana: true
```

- A service not listed is on. A name that is not in `service-values.yaml` fails
  the render.
- Turning a service off and re-running `deploy.sh` deletes its Application and
  what it created (prune).
- A service that is off still has to parse: its aliases need their globals.
- `consumer-adapter` and `provider-adapter` run only in multi-adapter mode, with
  network-adapter's multi-adapter values. `otel-collector` replaces `clickstack`
  and `otel-agent`; turn those off when it goes on.

## Deploy

```bash
./scripts/deploy.sh dev --dry-run   # the Applications it would apply
./scripts/deploy.sh dev             # apply them, then OpenBao, secrets, keyId
```

`deploy.sh` renders this chart from the local `config/dev/` files and applies
one Application per service, each pointing at its chart in helmcharts at
`argocd.targetRevision` with its values inlined. From there:

- **A chart change** deploys by merge: Argo CD watches `targetRevision`.
- **A config change** (anything in `config/dev/`) deploys by re-running
  `./scripts/deploy.sh dev`: the files are not in git, so Argo CD cannot see
  them. Applications for services that are now off are pruned.

Preview the full Applications:

```bash
helm template oan config -f config/dev/argocd.yaml
```

Each Application gets a sync wave from its position in `service-values.yaml`, so
they are created in install order. Argo CD only waits for each to be healthy if
its `argocd-cm` has a health check for `argoproj.io/Application`; without one the
services come up together and self-heal settles them.

## Secrets

Credentials are generated locally, kept out of git, and stored in OpenBao, which
is the source of truth. External Secrets Operator turns them into Kubernetes
Secrets (the `openbao-secrets` service).

```bash
./scripts/secrets.sh dev setup      # once per cluster, after openbao is running
./scripts/secrets.sh dev generate   # writes secrets/secrets-dev.yaml (gitignored, 0600)
./scripts/secrets.sh dev push       # writes changed entries to OpenBao
./scripts/secrets.sh dev key-ids    # after registry-seed: keyId, push, restart adapters
./scripts/secrets.sh dev pull       # lost the file? rebuild it from OpenBao
./scripts/secrets.sh dev list       # names and versions, never values
```

- `generate` refuses to overwrite the file: regenerating changes every password
  while the cluster keeps the old ones. Use `pull` to recover it, or
  `generate --missing` to add a newly introduced secret.
- `push`, `pull`, `list` and `key-ids` fetch their own 15-minute operator token
  (needs the cluster's admin access level), unless `BAO_TOKEN` is set.
- `setup` writes the unseal key to `~/openbao-<env>-unseal.key` and the root
  token and recovery keys to `~/openbao-<env>-init.json`. Move both into the
  password manager.

## New cluster

```bash
./scripts/deploy.sh <env>
```

1. Checks the kubectl context (and asks), that Argo CD is installed, and that
   the cert-manager and CloudNativePG CRDs are applied (server side, once).
2. Renders `config/<env>/` and applies the Applications: Argo CD deploys every
   service.
3. `secrets.sh <env> setup`: unseal key, init and configure OpenBao. Skipped
   once it is configured.
4. Generates the secrets file for an empty store, or pulls it from a store that
   already has values, then `push`es whatever changed.
5. Waits for registry-seed, then `secrets.sh <env> key-ids`: keyId into the
   file and OpenBao, adapters restarted.

Safe to re-run. `--skip-seed` stops after step 4; `--dry-run` lists the
Applications without touching the cluster.

## knowledge-provider and langfuse

Optional: set them `false` in `applications:` until needed.

- **knowledge-provider** (namespace `knowledge-provider`): its own Keycloak and
  Temporal, each with a CNPG database; MinIO and Qdrant (release names `minio`
  and `qdrant`, which the api and worker expect); then the api, worker and UI,
  pinned to `v0.2.0`. Auth is off and there is no ingress until the domain is
  set. The worker is pinned to the api's node: they share the `sqlite`,
  `documents` and `hf-cache` volumes, which are ReadWriteOnce.
- **langfuse** (namespace `langfuse`): every credential comes from OpenBao. Left
  to the chart they are generated with `lookup`, which Argo CD cannot see, so
  they would change on every sync. The ClickHouse operator's CRDs are too large
  for a client-side apply; apply them once, server side, before langfuse syncs:

  ```bash
  helm template ch charts/langfuse/charts/clickhouse-operator-helm \
    --show-only templates/crd/clickhouseclusters.clickhouse.com.yaml \
    --show-only templates/crd/keeperclusters.clickhouse.com.yaml | kubectl apply --server-side -f -
  ```

Their secrets (`knowledge-provider-*`, `langfuse-*`) are generated with the rest.
An environment whose secrets file already exists adds them with:

```bash
./scripts/secrets.sh dev generate --missing && ./scripts/secrets.sh dev push
```

The knowledge-provider API keys (Mistral, embeddings, translation, HF and so
on) are optional and not generated: add a `knowledge-provider-provider-keys`
entry to the secrets file and to `openbao-secrets` when you have them.

## VM (k3s)

Set these in `global-values.yaml`:

```yaml
  storageClass: &storageClass local-path
  loadBalancerAnnotations: &loadBalancerAnnotations {}
```
