# adapter-service

One chart for all three Beckn adapters. `provider`, `network` and `experience`
are the same image and the same config format, so `role` selects the handler
role, the step list and where requests are routed.

```
  experience ──unsigned──▶ network ──▶ discovery
                              │
    provider ──signed─────────┤
        │                     └── registry (whose key signed this?)
        └──▶ mandi / agmarknet upstreams
```

| Role | Handler | Steps | Routes to |
|---|---|---|---|
| `provider` | `bpp` | `validateSign → addRoute → sign` | upstream APIs (several) |
| `network` | `bpp` | `validateSign → addRoute → sign` | discovery |
| `experience` | `bap` | `addRoute → sign` | network |

`experience` is the only one that accepts **unsigned** requests: the experience
app is inside the trust boundary, so there is no network signature to check.
That is why it has no `validateSign`, and why an Ingress on it exposes an
unauthenticated entry point.

Stateless. Each adapter's identity is a keypair in a Secret; everything else it
reads from the registry at request time.

## Install

Once per role. **Keep the release name per-role** — it is what the Service is
called, which is the DNS name the other adapters address.

```sh
helm dependency build charts/adapter-service

helm upgrade --install provider-adapter charts/adapter-service -n oan \
  -f charts/adapter-service/examples/provider.yaml
helm upgrade --install network-adapter charts/adapter-service -n oan \
  -f charts/adapter-service/examples/network.yaml
helm upgrade --install experience-adapter charts/adapter-service -n oan \
  -f charts/adapter-service/examples/experience.yaml
```

Each example sets `fullnameOverride` to `<role>-adapter`. Without it the Service
would be named `<release>-adapter-service`, and nothing routing by name would
resolve. Rename a release and you must update whatever routes to it — `experience`
points at the network adapter, `network` points at discovery.

The Secret comes first — the render fails without it.

## What you must decide

Six values have no default, and the chart fails rather than guessing. Each one
is something that produces a *silent* failure if wrong, which is why it is a
render error rather than a default:

| Value | Why there is no default |
|---|---|
| `image.repository` | An empty one renders `ghcr.io/:tag`, which Helm and the API server both accept and which surfaces later as `ImagePullBackOff` |
| `keys.existingSecret.name` | Without an identity the pod starts, serves `/health`, reports Ready, and fails every signature it makes — in a peer's logs |
| `registry.url` | The adapter verifies every caller against the registry, so with none it can verify nobody |
| `role` | It decides the handler role, the step list and the routing target. A default would give one adapter another one's behaviour |
| `routing.rules` | An adapter with no route accepts requests and has nowhere to send them |
| `otel.endpoint` | Only when `otel.enabled` — an enabled exporter with nowhere to send logs a failure every interval |

## The identity Secret

Six keys, all required:

```sh
kubectl -n oan create secret generic <role>-adapter-keys \
  --from-literal=subscriberId=... \
  --from-literal=keyId=... \
  --from-literal=signingPrivateKey=... \
  --from-literal=signingPublicKey=... \
  --from-literal=encrPrivateKey=... \
  --from-literal=encrPublicKey=...
```

`keyId` is the **key's osid as the registry assigned it**, not a name you
choose — that is what a verifier looks the key up by.

In the compose stack `bin/setup.py` generates these and writes them to
`keys/keys.json`. Check the field names there before scripting the command
above; that file's shape belongs to `setup.py`, not to this chart.

### How the keys reach the process

The adapter reads one config file and wants the keys inline in it. A plain
ConfigMap would therefore hold private keys, so instead:

1. the **ConfigMap** holds the config with `__NETWORK_*__` placeholders — the
   same shape as the `.tmpl` in the compose stack;
2. the **Secret** is mounted as files, not env vars, so the values are not
   readable from `/proc/<pid>/environ` of anything in the pod;
3. an **init container** substitutes one into the other and writes the result
   to an `emptyDir` that dies with the pod.

It fails loudly if a key is empty or a placeholder survives. That check exists
because the alternative is an adapter that runs, looks healthy, and produces
signatures nobody can verify — a failure that shows up in someone else's logs.

## Key rotation needs a restart

The keys Secret is not rendered by this chart, so its contents cannot go into
the config checksum, so changing it restarts nothing:

```sh
kubectl -n oan rollout restart deploy/<role>-adapter
```

Changing anything else — log level, upstreams, telemetry — rolls the pods on
its own.

## The registry row

This chart cannot verify the half of the setup that lives outside the cluster.
The adapter's `subscriberId` needs a `Participant` row in the registry carrying
the **public** half of the keypair. Missing, or carrying a different key, and
every peer rejects what this adapter signs while the pod stays perfectly
healthy.

## Values

See `values.yaml` — every field is commented with what it does and what breaks
without it. `examples/{provider,network,experience}.yaml` are working dev deployments;
`ci/` holds the two files lint renders, one minimal and one with every switch
turned on.

## Related

- `charts/discovery` — where `discover` and `publish` are forwarded
- `charts/registry` — what signatures are verified against
- `docker-deployment/` — the compose stack this chart was modelled on
