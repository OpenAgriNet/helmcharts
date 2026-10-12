# adapter-service

One chart for the Beckn adapters, in two topologies. **single (the default):**
the Network Adapter Layer, role `network-adapter-layer`, all three tiers in one
adapter. **multi (optional):** three roles, `provider`, `network` and
`consumer`, one release per tier. All roles are the same image and the same
config format, so `role` selects the handler role, the step list
and where requests are routed.

```
  consumer ──unsigned──▶ network ──▶ discovery
                              │
    provider ──signed─────────┤
        │                     └── registry (whose key signed this?)
        └──▶ mandi / agmarknet upstreams
```

```
  network-adapter-layer (one adapter, one pod):
    /<action> ──lo──▶ /network/ ──▶ discovery
        └──────lo────▶ /provider/ ──▶ upstream APIs
    /provider/publish ──lo──▶ /network/publish
```

| Role | Handler | Steps | Routes to |
|---|---|---|---|
| `provider` | `bpp` | `validateSign → addRoute → sign` | upstream APIs (several) |
| `network` | `bpp` | `validateSign → addRoute → sign` | discovery |
| `consumer` | `bap` | `addRoute → sign` | network |
| `network-adapter-layer` | `bap` + `bpp` (four modules) | each module runs its own tier's steps | discovery and the upstream APIs; tier hops are loopback HTTP inside the pod |

`consumer` is the only one that accepts **unsigned** requests: the consumer
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
helm upgrade --install consumer-adapter charts/adapter-service -n oan \
  -f charts/adapter-service/examples/consumer.yaml
```

Each example sets `fullnameOverride` to `<role>-adapter`. Without it the Service
would be named `<release>-adapter-service`, and nothing routing by name would
resolve. Rename a release and you must update whatever routes to it — `consumer`
points at the network adapter, `network` points at discovery.

The Secret comes first — the render fails without it.

## What you must decide

Six values have no default, and the chart fails rather than guessing. Each one
is something that produces a *silent* failure if wrong, which is why it is a
render error rather than a default:

| Value | Why there is no default |
|---|---|
| `image.repository` | An empty one renders `ghcr.io/:tag`, which Helm and the API server both accept and which surfaces later as `ImagePullBackOff` |
| `keys.existingSecret.name` | Without an identity the pod starts, serves `/health`, reports Ready, and fails every signature it makes — in a peer's logs. Role `network-adapter-layer` uses `networkAdapterLayer.keys.{consumer,network,provider}.existingSecret` instead (three identities) |
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

## Choosing a topology: single (default) or multi (optional)

| Topology | Releases (release = namespace) | Callers use |
|---|---|---|
| **single (default)**: Network Adapter Layer | `network-adapter` (examples/network-adapter.yaml) | `network-adapter:9200/<action>`, `network-adapter:9200/provider/publish` |
| multi (optional) | `consumer-adapter`, `network-adapter`, `provider-adapter` (examples/consumer.yaml, network.yaml, provider.yaml) | `consumer-adapter:9202/<action>`, `provider-adapter:9200/publish` |

`role: network-adapter-layer` runs all three tiers in one adapter, as four
modules of one process. Its config is `config/network-adapter-config.yaml`
(each module is its tier's module from `config/<tier>-config.yaml`, so a change
to a tier file that should apply to both modes goes in both files). Its routing
is **generated** at render time from `config/routing-*.yaml`, so routes stay in
one place. Tier hops are loopback HTTP inside the pod (any image). It signs as
the same three identities, from the same three Secrets, so the registry is the
same in both modes. An environment's `config:` override for a multi role is NOT
seen by the Network Adapter Layer; give its release its own `config:` if
needed (it replaces `network-adapter-config.yaml` whole).

Pick the topology per environment with ONE value where releases are chosen,
`networkAdapterTopology: single | multi` (a helmfile environment value; no chart
reads it; **default `single`**). `single` installs one release, `network-adapter`
in namespace `network-adapter-layer` (the Network Adapter Layer); `multi`
installs the three tier releases. For a helmfile (adapt to
infra-automation's layout):

```yaml
# environments: values may carry   networkAdapterTopology: multi      # default: single
{{- $topology := .Values.networkAdapterTopology | default "single" }}
{{- if not (has $topology (list "single" "multi")) }}
{{- fail (printf "networkAdapterTopology must be single or multi, not %q" $topology) }}
{{- end }}
releases:
{{- range $tier := list "consumer" "network" "provider" }}
  - name: {{ $tier }}-adapter
    namespace: {{ $tier }}-adapter
    chart: ../helmcharts/charts/adapter-service
    installed: {{ eq $topology "multi" }}
    values: [values/{{ $tier }}-adapter.yaml]
{{- end }}
  - name: network-adapter
    namespace: network-adapter-layer
    chart: ../helmcharts/charts/adapter-service
    installed: {{ eq $topology "single" }}
    values: [values/network-adapter.yaml]
```

With Argo CD, gate the same releases on the same value (for example an
ApplicationSet list generator, or `{{ if }}` in an app-of-apps chart).

**Namespace `network-adapter-layer`.** The Network Adapter Layer is the network
adapter as a whole, so its release, Service and app name are `network-adapter`,
the same as the multi network tier's. Its **namespace** is its own,
`network-adapter-layer`, for two reasons: openbao-secrets mirrors all three
tier identities into it, which must not happen in the multi network tier's
namespace `network-adapter`; and the two topologies can then run side by side
during a switch-over. In-cluster address of the layer:
`network-adapter.network-adapter-layer.svc.cluster.local:9200`.

To switch an environment: install the layer (namespace `network-adapter-layer`)
next to the multi releases, repoint the app (`…:9200/<action>`) and the
catalogue publisher (`…:9200/provider/publish`), then remove the three multi
releases. Both topologies sign with the same identities and the same registry
rows, so nothing in the registry changes. Reverse the steps to switch back.

## Values

See `values.yaml` — every field is commented with what it does and what breaks
without it. `examples/{provider,network,consumer}.yaml` are working dev deployments for the
multi-adapter mode, and `examples/network-adapter.yaml` for the Network Adapter Layer;
`ci/` holds the files lint renders, including `ci/network-adapter-values.yaml`.

## Related

- `charts/discovery` — where `discover` and `publish` are forwarded
- `charts/registry` — what signatures are verified against
- `docker-deployment/` — the compose stack this chart was modelled on
