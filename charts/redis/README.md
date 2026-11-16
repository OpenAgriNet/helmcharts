# redis

One Redis pod holding the
[decision-support-system](https://github.com/OpenAgriNet/decision-support-system)'s
form-session state: which form is waiting for an OTP, and the per-session lock
that keeps two turns from running over each other (DSS ADR-0022,
`docs/ADR/0022-session-state-for-form-flows.md`). It runs the same command as
the `redis` service in DSS's `docker-compose.yml`, plus a memory ceiling.

## What it is not

- **Not persistent.** `--save ""` and `--appendonly no` turn off RDB and AOF,
  there is no volume, and the root filesystem is read-only. A crash, an
  upgrade or a reschedule brings it back **empty**: every open form flow is
  lost and the farmer is asked to start again. ADR-0022 accepts that; form
  values must never land on disk.
- **Not HA.** One replica, fixed - not a value. A second pod would be a second,
  separate Redis behind the same Service, and the DSS's lock only holds when
  every turn reaches the same server. No Sentinel, no cluster, no operator.
  Failover is Kubernetes restarting the pod: the Deployment brings it back
  when it dies, and the probes restart it when it stops answering. Replication
  and automatic failover come later, if a need for them shows up.
- **Not exposed.** `ClusterIP` only; there is no Ingress.
- **Not TLS.** ADR-0022 asks for TLS; this first pass goes without it, and the
  password and form values cross the cluster network in clear text, as every
  other in-cluster service here does today except OpenBao.
- **Not Langfuse's Redis.** That one is tuned for Langfuse and saves to disk.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | 1 replica, `Recreate`; `redis-cli ping` probes; resources mandatory |
| Service | `ClusterIP`, port `redis` 6379 |
| ServiceAccount | `automountServiceAccountToken: false` |

No Secret and no ConfigMap: Redis is configured by flags, and the password
comes from a Secret you create.

## The Secret it needs

The chart renders no Secret ([CONVENTIONS.md](../../CONVENTIONS.md#secrets))
and has no passwordless mode: with `auth.existingSecret` empty the render
fails. Create the Secret first, in the release's namespace:

```bash
kubectl -n decision-support-system create secret generic redis \
  --from-literal=password=<password>
```

Keep the password to letters, digits, `-` and `_`. The DSS puts it into
`DSS_REDIS_URL` as it is, and anything else would need URL-encoding.

| Value | Default | Meaning |
|---|---|---|
| `auth.existingSecret` | `""` (required) | Name of the Secret |
| `auth.passwordKey` | `password` | Key inside it |

## Install

```bash
helm install redis charts/redis -n decision-support-system \
  -f charts/redis/examples/redis.dev.yaml
```

Then check it answers (`NOTES.txt` prints the same):

```bash
kubectl -n decision-support-system exec deploy/redis -- \
  sh -c 'redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping'
```

## How the DSS connects

The DSS reads one setting, `DSS_REDIS_URL`. Unset, it runs without a store and
a form that needs an OTP cannot finish. Put the full URL in a Secret of its own
and hand it to the DSS chart through its existing `secretEnv`:

```bash
kubectl -n decision-support-system create secret generic dss-redis-url \
  --from-literal=url='redis://:<password>@redis.decision-support-system.svc.cluster.local:6379/0'
```

```yaml
# decision-support-system values
secretEnv:
  DSS_REDIS_URL:
    name: dss-redis-url
    key: url
```

Use the FQDN, `redis.<namespace>.svc.cluster.local`, so the URL still resolves
if the DSS and this Redis end up in different namespaces. The password appears
in two Secrets; that is the cost of the DSS taking a URL rather than a
password, and both must change together.

## Memory

| Value | Default | Why |
|---|---|---|
| `maxmemory` | `100mb` | Without a ceiling Redis grows until the container limit kills it - and a killed Redis restarts empty. Keep it well under `resources.limits.memory` (256Mi). |
| `maxmemoryPolicy` | `allkeys-lru` | Every DSS key carries a TTL, so this evicts the same keys `volatile-lru` would, but it also covers a key written without one by mistake, which `volatile-*` would never evict and `noeviction` would turn into failed writes. |

DSS entries live 180 seconds and are small, so the default is generous.

## Security

Runs as the image's `redis` user - uid 999, gid 1000 in the alpine image -
with `runAsNonRoot`, a read-only root filesystem, all capabilities dropped,
`allowPrivilegeEscalation: false` and the `RuntimeDefault` seccomp profile.
The container's `command` replaces the image's entrypoint script, so nothing
starts as root to drop privileges later.

## Validation

```bash
./scripts/lint-charts.sh
helm template redis charts/redis -f charts/redis/ci/lint-values.yaml
```

Render-time guardrails:

- `auth.existingSecret` or `auth.passwordKey` empty (this chart)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)

## Versioning

Every change needs a `version` bump in `Chart.yaml` and an entry in
[`CHANGELOG.md`](./CHANGELOG.md) — see [`CONVENTIONS.md`](../../CONVENTIONS.md).
