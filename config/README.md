# config

Everything one environment runs, deployed by Argo CD. This directory is itself a
Helm chart: it renders one Argo CD Application per service.

```
config/
  Chart.yaml, values.yaml, templates/   the chart (app of apps)
  <env>/argocd.yaml                     the root Application for <env>
  <env>/global-values.yaml              values many services share, each a YAML anchor
  <env>/service-values.yaml             every service in install order, with its values
  <env>/resource-values.yaml            each service's resources (requests/limits)
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
- A global set to `null` (a prod key not issued yet) fails the render until it is
  set under `global:` in that environment's `argocd.yaml`.
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
- Turning a service off deletes its Application and what it created (prune).
- A service that is off still has to parse: its aliases need their globals.
- `consumer-adapter` and `provider-adapter` run only in multi-adapter mode, with
  network-adapter's multi-adapter values. `otel-collector` replaces `clickstack`
  and `otel-agent`; turn those off when it goes on.

## Deploy

```bash
./scripts/deploy.sh dev     # once per cluster: Argo CD, OpenBao, secrets, keyId (see New cluster)
```

After that a deploy is a merge: Argo CD watches the root Application's
`targetRevision`, re-renders this chart, and syncs each service. Both
`targetRevision` lines in `argocd.yaml` must match.

Preview what Argo CD will create:

```bash
helm template oan config --set environment=dev
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
2. `kubectl apply -f config/<env>/argocd.yaml`: Argo CD deploys every service.
3. `secrets.sh <env> setup`: unseal key, init and configure OpenBao. Skipped
   once it is configured.
4. Generates the secrets file for an empty store, or pulls it from a store that
   already has values, then `push`es whatever changed.
5. Waits for registry-seed, then `secrets.sh <env> key-ids`: keyId into the
   file and OpenBao, adapters restarted.

Safe to re-run. `--skip-seed` stops after step 4; `--dry-run` lists the
Applications without touching the cluster.

## VM (k3s)

Set these in `global-values.yaml`:

```yaml
  storageClass: &storageClass local-path
  loadBalancerAnnotations: &loadBalancerAnnotations {}
```
