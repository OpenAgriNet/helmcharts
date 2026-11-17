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
- **Not exposed.** `ClusterIP` only; there is no Ingress. A NetworkPolicy
  (on by default) lets only the DSS's pods connect - see
  [Who may connect](#who-may-connect).
- **Not TLS.** ADR-0022 asks for TLS; this first pass goes without it, and the
  password and form values cross the cluster network in clear text, as every
  other in-cluster service here does today except OpenBao. The NetworkPolicy
  is what limits who can try.
- **Not Langfuse's Redis.** That one is tuned for Langfuse and saves to disk.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | 1 replica, `Recreate`; `redis-cli ping` probes; resources mandatory |
| Service | `ClusterIP`, port `redis` 6379 |
| ServiceAccount | `automountServiceAccountToken: false` |
| NetworkPolicy | ingress on 6379 from the DSS's pods only; `networkPolicy.enabled` |

No Secret and no ConfigMap: Redis is configured by flags, and the password
comes from a Secret you create.

## The Secret it needs

The chart renders no Secret ([CONVENTIONS.md](../../CONVENTIONS.md#secrets))
and has no passwordless mode: with `auth.existingSecret` empty the render
fails. Create the Secret first, in the release's namespace. The examples use
`dss`, the DSS's namespace, which is where OAN runs this chart.

One Secret holds both what Redis reads (`password`) and what the DSS reads
(`url`), so the two are created, and changed, together:

```bash
kubectl -n dss create secret generic redis \
  --from-literal=password=<password> \
  --from-literal=url='redis://:<password>@redis.dss.svc.cluster.local:6379/0'
```

Keep the password to letters, digits, `-` and `_`. It goes into the URL as it
is, and anything else would need URL-encoding. In OAN, `manage-secrets.py`
(infra-automation) generates this Secret as `dss-redis` and OpenBao holds it.

| Value | Default | Meaning |
|---|---|---|
| `auth.existingSecret` | `""` (required) | Name of the Secret |
| `auth.passwordKey` | `password` | Key inside it |

## Install

```bash
helm install redis charts/redis -n dss \
  -f charts/redis/examples/redis.dev.yaml
```

Then check it answers (`NOTES.txt` prints the same). `redis-cli` in the pod
authenticates from `REDISCLI_AUTH`:

```bash
kubectl -n dss exec deploy/redis -- redis-cli ping
```

## How the DSS connects

The DSS reads one setting, `DSS_REDIS_URL`. Unset, it runs without a store and
a form that needs an OTP cannot finish. Point the DSS chart's
`redis.urlSecret` at the `url` key (DSS chart 0.3.0 or later):

```yaml
# decision-support-system values
redis:
  urlSecret:
    name: redis
    key: url
```

Write the host as the FQDN, `redis.<namespace>.svc.cluster.local`, as
[CONVENTIONS.md](../../CONVENTIONS.md) asks. It resolves from any namespace;
the NetworkPolicy, not DNS, decides who gets in.

Use the URL with redis-py (what the DSS uses). `redis-cli -u` reads the empty
username in `redis://:<pw>@` as the username `""` and fails with `WRONGPASS`.
For redis-cli, use `-a`, `REDISCLI_AUTH` or `redis://default:<pw>@...`.

## Rotating the password

Both pods read the Secret once, when they start. Updating the Secret alone
changes nothing until a pod restarts, and if only one of them restarts, the
DSS and Redis disagree and every form that needs an OTP is refused. So:

1. Change `password` and `url` together.
2. Wait until the Secret in the release's namespace has the new values. In
   OAN, force-sync the ExternalSecret rather than waiting for its refresh.
3. Restart both, together:
   `kubectl -n dss rollout restart deploy/redis deploy/decision-support-system`.

Open form flows are lost, as on any Redis restart.

## Who may connect

`networkPolicy.from` lists the NetworkPolicy peers allowed on port 6379. The
default is pods labelled `app.kubernetes.io/name: decision-support-system` in
the release's namespace, which is what the DSS chart sets. Everything else,
including a debug pod you start next to it, is dropped. Test from inside the
Redis pod instead (`kubectl exec deploy/redis -- redis-cli ping`), or start the
debug pod with that label.

The render fails if the policy is on and `from` is empty: an ingress rule with
no peers lets every pod in. It needs a CNI that enforces NetworkPolicy; on EKS
that means turning on network policy in the VPC CNI. Without one, the object
is accepted and does nothing.

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

The password is never on a command line. The server gets it as config on
stdin (`redis-server -` and a here-document), and `redis-cli` reads it from
`REDISCLI_AUTH`, which the chart sets from the same Secret. So it is not in
the pod spec, `/proc/<pid>/cmdline` or `ps` on the node; only in the
container's environment.

## Validation

```bash
./scripts/lint-charts.sh
helm template redis charts/redis -f charts/redis/ci/lint-values.yaml
```

Render-time guardrails:

- `auth.existingSecret` or `auth.passwordKey` empty (this chart)
- `networkPolicy.enabled` with an empty `networkPolicy.from` (this chart)
- `resources` empty (`common`)
- a probe with no handler or with two (`common`)

## Versioning

Every change needs a `version` bump in `Chart.yaml` and an entry in
[`CHANGELOG.md`](./CHANGELOG.md) — see [`CONVENTIONS.md`](../../CONVENTIONS.md).
