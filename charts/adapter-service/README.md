# adapter-service

One chart for the OAN Beckn adapters. `role` picks the adapter (`provider-adapter`,
`network-adapter` or `consumer-adapter`; the old names `provider`, `network`, `consumer` still work); all three use the same image and config format.

Role `network` has two deployment modes, set by `deploymentMode`:

- **single (default):** one release runs the consumer, network and provider
  tiers as modules of one adapter. Tier-to-tier calls stay inside the pod.
- **multi:** one release per tier (`consumer-adapter`, `network-adapter`,
  `provider-adapter`). The network release sets `deploymentMode=multi-adapter`.

`provider-adapter` and `consumer-adapter` releases are always multi-adapter and ignore the value.

**To install, follow [SETUP.md](SETUP.md).** Why single is the default is in
[ADR-0001](../../docs/adr/0001-single-network-adapter-deployment-mode.md).

```
  multi:   app ──▶ consumer ──▶ network ──▶ discovery
                       └──────▶ provider ──▶ upstream APIs

  single:  app ──▶ network-adapter
                     /<action>            consumer module
                     /_internal/network/  network module   ──▶ discovery
                     /_internal/provider/ provider module  ──▶ upstream APIs
                     /publish             catalogue publishing
```

## Roles

| Role | Handler | Steps | Routes to |
|---|---|---|---|
| `provider-adapter` | `bpp` | `validateSign → addRoute → sign` | upstream APIs |
| `network-adapter` (multi-adapter) | `bpp` | `validateSign → addRoute → sign` | discovery |
| `consumer-adapter` | `bap` | `addRoute → sign` | network |
| `network-adapter` (single-adapter, default) | `bap` + `bpp`, four modules | each module runs its tier's steps | discovery and upstream APIs |

The consumer accepts **unsigned** requests: the app is inside the trust
boundary. An Ingress on it is an unauthenticated entry point, so rate-limit it.

Callers see the same paths in both modes: `/<action>` (e.g. `/discover`),
`/publish` and `/health`. `/_internal/` is only called by the adapter itself.
The chart refuses an Ingress path other than a single-segment `/<action>`.

## Required values

The render fails without these:

| Value | Why |
|---|---|
| `role` | Decides the handler role, the steps and where requests go. |
| `image.repository` | An empty one renders an image name that fails only at pull time. |
| `keys.existingSecret.name` | The adapter's identity (multi roles). Single mode uses `adapterKeys`, which defaults to the Secret names openbao-secrets creates. |

## The identity Secret

Six keys, all required:

```sh
kubectl -n <namespace> create secret generic <role>-adapter-keys \
  --from-literal=subscriberId=... \
  --from-literal=keyId=... \
  --from-literal=signingPrivateKey=... \
  --from-literal=signingPublicKey=... \
  --from-literal=encrPrivateKey=... \
  --from-literal=encrPublicKey=...
```

- `keyId` is the key's **osid as the registry assigned it**, not a name you choose.
- The registry needs a `Participant` row for `subscriberId` with the **public**
  key. If it is missing or different, peers reject every signature while the
  pod still reports Ready.
- The keys never go into the ConfigMap. The ConfigMap holds placeholders, and an
  init container fills them from the Secret into an `emptyDir` at pod start. It
  fails if a key is empty or a placeholder is left.

## Key rotation needs a restart

The chart does not render the keys Secret, so changing it restarts nothing:

```sh
kubectl -n <namespace> rollout restart deploy/<release>
```

Any other values change rolls the pods on its own.

## Catalog crawler

The network adapter's config files carry the catalogcrawler's `registry` and
`crawler` plugin blocks (`config/single-adapter/network-adapter-config.yaml`,
`config/multi-adapter/network-config.yaml`). On every tick it runs the pipeline
of each registry binding with a `publish` action and POSTs the catalogs to
`/publish` (this pod in single mode, the provider adapter in multi mode). When
the config carries the `__ADAPTER_CRAWLER_DB_DSN__` placeholder, the chart fills
it from `crawler.dbDsnKey` and mounts an emptyDir at `/tmp/catalogs`.

It needs, outside this chart:

- an image with the `catalogcrawler` plugin and the `render.mappingByField` engine;
- the `publish` action in the registry: the registry and registry-seed from #96, and
  `bindings[].publishPipelineUrl` in registry-seed, pinned to a commit SHA;
- its own run log database (PostgreSQL), not discovery's: e.g. a `catalogcrawler`
  database and role on the shared `postgres` cluster. Its DSN goes under
  `crawler.dbDsnKey` in the keys Secret (the network tier's Secret in single mode);
- the pipeline's inputs as env: `MANDI_API_URI`, `MANDI_PARTICIPANT_ID`,
  `APP_NETWORK_ID`, and the provider's Agmarknet credentials
  `AGMARKNET_ACCESS_NAME` and `AGMARKNET_PASSWORD`. Single mode already has those
  two from the provider's upstream Secret; in multi mode map them with `extraEnv`
  `secretKeyRef`s into that Secret rather than copying them into a new one.

To run without it, remove both blocks from the config file (or from `config`).

## Values and examples

- `values.yaml`: every field, with a comment on what it does.
- `examples/network-adapter.yaml`: role `network-adapter` in either mode (`--set deploymentMode=single-adapter|multi-adapter`).
- `examples/{provider,consumer}-adapter.yaml`: the other two releases in multi mode.
- `ci/*-values.yaml`: what CI lints and renders.

## Related

- `charts/openbao-secrets`: puts the key Secrets in each namespace.
- `charts/discovery`: where `discover` and `publish` are forwarded.
- `charts/registry`: what signatures are verified against.
- `quick-start/`: the same adapters in docker compose.
