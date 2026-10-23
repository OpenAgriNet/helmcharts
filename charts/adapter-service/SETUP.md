# Network adapter setup guide

How to install the network adapter with this chart, in either deployment mode.
Why there are two modes is in
[ADR-0001](../../docs/adr/0001-single-network-adapter-deployment-mode.md).

## Guidelines

1. **Use single mode unless you need multi.** Single is the default: one release
   runs the consumer, network and provider tiers. Multi runs one release per tier.
2. **Run one mode per environment.** Both modes use release `network-adapter` in
   namespace `network-adapter`.
3. **Switch with one value.** The `network-adapter` release always uses the same
   command; only `--set deploymentMode=single-adapter|multi-adapter` changes
   (default `single-adapter`). `provider` and `consumer` ignore it.
4. **Keep the release and namespace names.** Adapters find each other by
   Service name, so `consumer-adapter`, `network-adapter` and `provider-adapter`
   must not be renamed.
5. **Keep each tier's keys where they belong.** In single mode `network-adapter`
   holds all three tiers' keys. In multi mode it holds only its own.
6. **Never expose `/publish` or `/_internal/` outside the cluster.** Only
   `/<action>` paths (for example `/discover`) may go on an Ingress.

| | single (default) | multi |
|---|---|---|
| Releases | `network-adapter` | `consumer-adapter`, `network-adapter`, `provider-adapter` |
| App calls | `network-adapter.network-adapter.svc:9201/<action>` | `consumer-adapter.consumer-adapter.svc:9202/<action>` |
| Catalogue publisher calls | `network-adapter.network-adapter.svc:9201/publish` | `provider-adapter.provider-adapter.svc:9200/publish` |

The network release, in either mode:

```sh
helm upgrade --install network-adapter charts/adapter-service \
  -n network-adapter --create-namespace \
  -f charts/adapter-service/examples/network-adapter.yaml \
  --set deploymentMode=single-adapter   # or multi-adapter
```

## Single mode (default)

### 1. Secrets

These four Secrets must exist in namespace `network-adapter`.
The default openbao-secrets values already mirror all four there.

| Secret | Holds |
|---|---|
| `consumer-adapter-keys` | consumer tier keys |
| `network-adapter-keys` | network tier keys |
| `provider-adapter-keys` | provider tier keys |
| `provider-upstream` | provider upstream credentials (`adapterKeys.upstreamSecret`) |

Each keys Secret has the six keys described in [README.md](README.md#the-identity-secret),
and each tier's public key must be in its registry row.

### 2. Install

```sh
helm dependency build charts/adapter-service
helm upgrade --install network-adapter charts/adapter-service \
  -n network-adapter --create-namespace \
  -f charts/adapter-service/examples/network-adapter.yaml \
  --set deploymentMode=single-adapter
```

If the config needs extra values from a tier's Secret, list them per
environment. Otherwise the init container stops on the unfilled placeholder.

```yaml
adapterKeys:
  extraSubstitutions:
    __VISTAAR_TOKEN_URL__: provider/vistaarTokenUrl   # <tier>/<key in that tier's Secret>
```

### 3. Check

```sh
kubectl -n network-adapter rollout status deploy/network-adapter
kubectl -n network-adapter port-forward svc/network-adapter 9201:9201
curl localhost:9201/health        # in a second terminal
```

## Multi mode

### 1. Secrets

Each tier reads its keys from its own namespace, and `network-adapter` must not
keep the other tiers' keys. In the openbao-secrets values for the environment,
remove `network-adapter` from the consumer, provider and upstream entries:

```yaml
  - name: consumer-adapter-keys
    namespaces: [consumer-adapter]
  - name: network-adapter-keys
    namespaces: [network-adapter]
  - name: provider-adapter-keys
    namespaces: [provider-adapter]
  - name: provider-upstream
    namespaces: [provider-adapter]
```

Edit these entries in place in the values file. Do not pass this block on its
own with `-f`: Helm replaces the whole `secrets` list. Then delete the copies
already in `network-adapter`:

```sh
kubectl -n network-adapter delete secret consumer-adapter-keys \
  provider-adapter-keys provider-upstream --ignore-not-found
```

`examples/provider-adapter.yaml` reads its upstream credentials from a Secret named
`provider-adapter-upstream`. Create it in `provider-adapter`, or change
`envFromSecrets` in your provider values to `provider-upstream`.

### 2. Install

```sh
helm upgrade --install provider-adapter charts/adapter-service \
  -n provider-adapter --create-namespace -f charts/adapter-service/examples/provider-adapter.yaml
helm upgrade --install network-adapter charts/adapter-service \
  -n network-adapter --create-namespace -f charts/adapter-service/examples/network-adapter.yaml \
  --set deploymentMode=multi-adapter
helm upgrade --install consumer-adapter charts/adapter-service \
  -n consumer-adapter --create-namespace -f charts/adapter-service/examples/consumer-adapter.yaml
```

The network release is the same command as in single mode, with `multi-adapter`.
Without `--set` it would install as single and wait for keys it should not have.

### 3. Point callers at the new addresses

- App: `consumer-adapter.consumer-adapter.svc:9202/<action>`
- Catalogue publisher: `provider-adapter.provider-adapter.svc:9200/publish`

## Switching modes

A switch upgrades the `network-adapter` release in place. Both modes use the
same keys and registry rows, so the registry does not change.

**Single to multi:** do the multi steps above, in order.

**Multi to single:**

1. Put `network-adapter` back on the consumer, provider and upstream
   openbao-secrets entries.
2. Run the network release command with `single`:
   ```sh
   helm upgrade --install network-adapter charts/adapter-service \
     -n network-adapter -f charts/adapter-service/examples/network-adapter.yaml \
     --set deploymentMode=single-adapter
   ```
3. Point the app and the publisher at `network-adapter.network-adapter.svc:9201`.
4. Remove the other two releases:
   ```sh
   helm uninstall consumer-adapter -n consumer-adapter
   helm uninstall provider-adapter -n provider-adapter
   ```

## If something is wrong

| Symptom | Cause |
|---|---|
| Pod stuck in `ContainerCreating` | A Secret it mounts is missing from the namespace. |
| Init container `render-config` exits | A placeholder has no value; add it to `extraSubstitutions`. |
| `401` or `400` on `/_internal/...` from outside the pod | Expected: those paths only accept the adapter's own signed calls. |
| `helm` fails with `deploymentMode must be single-adapter or multi-adapter` | The value is misspelt. |

## quick-start (docker compose)

The same two modes run locally with `make`; see `quick-start/README.md`.

| | single (default) | multi |
|---|---|---|
| Start | `make up` | `NETWORK_ADAPTER_DEPLOYMENT_MODE=multi-adapter make up` |
| App calls | `127.0.0.1:9201/<action>` | `127.0.0.1:9202/<action>` |
| Publisher | `127.0.0.1:9201/publish` | `127.0.0.1:9200/publish` |
