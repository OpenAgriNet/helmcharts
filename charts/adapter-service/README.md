# adapter-service

One chart for the OAN Beckn adapters. `role` picks the adapter (`provider`,
`network` or `consumer`); all three use the same image and config format.

Role `network` has two deployment modes, set by `networkAdapterDeploymentMode`:

- **single (default):** one release runs the consumer, network and provider
  tiers as modules of one adapter. Tier-to-tier calls stay inside the pod.
- **multi:** one release per tier (`consumer-adapter`, `network-adapter`,
  `provider-adapter`). The network release sets `networkAdapterDeploymentMode=multi`.

`provider` and `consumer` releases are always multi and ignore the value.

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
| `provider` | `bpp` | `validateSign → addRoute → sign` | upstream APIs |
| `network` (multi) | `bpp` | `validateSign → addRoute → sign` | discovery |
| `consumer` | `bap` | `addRoute → sign` | network |
| `network` (single, default) | `bap` + `bpp`, four modules | each module runs its tier's steps | discovery and upstream APIs |

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
| `keys.existingSecret.name` | The adapter's identity (multi roles). Single mode uses `networkTierKeys`, which defaults to the Secret names openbao-secrets creates. |

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

## Values and examples

- `values.yaml`: every field, with a comment on what it does.
- `examples/network.yaml`: role `network` in either mode (`--set networkAdapterDeploymentMode=single|multi`).
- `examples/{provider,consumer}.yaml`: the other two releases in multi mode.
- `ci/*-values.yaml`: what CI lints and renders.

## Related

- `charts/openbao-secrets`: puts the key Secrets in each namespace.
- `charts/discovery`: where `discover` and `publish` are forwarded.
- `charts/registry`: what signatures are verified against.
- `quick-start/`: the same adapters in docker compose.
