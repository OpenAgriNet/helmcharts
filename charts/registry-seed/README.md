# registry-seed

Seeds the OAN registry with everything the network needs before it can answer a
request, then reports the key osid each adapter must be configured with.

It is a port of `quick-start/bin/setup.py`'s `seed()` and `key_osids()` to the
cluster. In compose those run on the VM because the registry publishes on
loopback only and there is no second way to reach it; here they run as a Job
inside the cluster, for the same reason.

## What it writes

| Rows | What they say |
|---|---|
| **Adapter identities** (`Participant`, `type: node`) | who each adapter is, and the public half of the signing key every signature it makes is verified against |
| **Upstreams** (`Participant`, `type: upstream`) | the ordinary APIs the provider adapter calls. No role, no keys, no credential — an upstream has never heard of Beckn |
| **Bindings** (`ProviderSchema`) | who answers which capability, over what method and path, with which mapping |
| **Schemas** (`SchemaRegistry`) | what each capability *means* — the pack in `network-specs` defining its attributes. One row per capability, so two providers of one capability share it |

## The constraint everything here is shaped by

**The registry is append-only.** It cannot update a record, and its delete is
*soft* and keeps the unique index — so an id is never reusable, and a row seeded
from a wrong value can never be corrected or replaced, only abandoned with the
capability recovered under a new id.

Three consequences, all deliberate:

- The chart **fails the render** on a missing `participantId`, `baseUrl`,
  `signingPublicKey`, `path` or `mappingUrl` rather than defaulting it. A
  render-time refusal is cheap; a successful write of a wrong value is not.
- Every write **checks first** and leaves what it finds alone, so re-running is
  safe and reports `already present`.
- The read-back **verifies the published key** still matches the one you hold.
  A participant seeded against an earlier keypair keeps that public key forever;
  signing with a new private half then produces signatures nobody can verify,
  and it surfaces much later as an authentication error with no obvious cause.
  The Job fails instead, naming the participant.

## keyId, and why it is a second step

`keyId` is the **osid the registry assigns** when the public key is written. It
cannot be generated in advance — `manage-secrets.py` emits it as `PENDING`
for exactly this reason — so the order is forced:

```
registry up ──▶ this Job ──▶ read keyId from its log ──▶ adapter releases
```

The Job prints one line per adapter. Set `keys.keyId` on each
`adapter-service` release to its own value.

## Values

`examples/seed.dev.yaml` carries the same seven participants, four bindings and
four capability schemas the compose stack seeds, so a cluster deployment starts
from a known-good set rather than a blank one. What it cannot carry is the
handful of values that are deployment facts — the addresses peers reach this
deployment at, the real upstream hosts, and two upstream paths that have no
default in `setup.py` either. Those ship as `<angle-bracketed>` placeholders and
the chart **refuses to render** until each is replaced, listing every one still
outstanding in a single message.

## Install

```bash
helm install registry-seed charts/registry-seed \
  -n registry -f my-values.yaml

kubectl -n registry logs job/registry-seed-<hash>
```

The Job name carries a hash of the seed document, so a values change creates a
new Job rather than failing on `Job.spec.template` being immutable.

## Values worth reading before the first run

| Value | Why it matters |
|---|---|
| `adapters[].signingPublicKey` | Bare base64, **no `base64:` prefix** — it is published verbatim and a verifier hands it straight to a decoder. The chart refuses a prefixed value. |
| `adapters[].baseUrl` | The address a **peer** posts to. Nothing in-cluster resolves it, so a wrong value costs nothing locally and everything on a shared network. |
| `bindings[].participantId\|capability` | Must match what the provider adapter is configured with. A mismatch is answered `404 this module serves no capability matching the request`, which names the request rather than the mismatch. |
| `schemaPackRef` | The registry **never fetches** `schemaUrl` — it only checks its shape. A ref that does not serve the pack stores happily and fails later, at whoever resolves it. |
| `seedUser.passwordSecret` | The account that can WRITE participants. Required; this chart renders no passwords. |

## Security

The Job needs no Kubernetes API access — it talks to the registry over HTTP —
so it gets its own ServiceAccount with `automountServiceAccountToken: false`,
runs as non-root with a read-only root filesystem, and drops all capabilities.

The seed document is a ConfigMap and holds no credential. The one credential,
the seed user's password, comes from a Secret through the environment.
