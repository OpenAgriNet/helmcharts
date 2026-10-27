# federation

Publishes what another network reads before it will talk to this one, and
admits another network.

```
.well-known/openagrinet        the signed Network Descriptor
.well-known/jwks.json          the PUBLIC half of this network's signing key
.well-known/schema-packs.json  the capability packs this network publishes
```

## Two parts, for one reason

Signing and serving are separate because the key and the documents belong in
different places.

```
Secret (the adapter's)  →  Job: sign  →  ConfigMap  →  server  →  /.well-known/
```

The Job mounts the **same Secret the adapter reads**. That is not an economy:
`oanfed publish` refuses to sign with a key the registry does not publish for
this network, so sharing it is what guarantees the descriptor names the key
that signs traffic. A mismatch there surfaces much later, as another network
failing to verify anything we send.

The private key exists only inside the Job, for the seconds it runs. What
reaches the ConfigMap is public by design — the descriptor, the public half of
the key, and a list of URLs.

## Revisions

Every run publishes the next revision, read from what the ConfigMap already
holds. `oanfed` refuses a revision that does not increase on the one served,
and so does every network holding the old one — so the number cannot come from
a value in git that a re-run would repeat.

Re-run it whenever the registry entry changes: a new base URL, a rotated key,
another schema pack. It is a `post-install,post-upgrade` hook, so `helm upgrade`
is enough.

## Admitting another network

```yaml
admissions:
  - networkId: net-b.example.org
    direction: source        # source = we may read from them
    schemaPacks:
      - https://.../AgricultureFacility/v0.1/attributes.yaml
```

One Job per entry. Declarative on purpose: who we agreed to talk to is
reviewable in git, rather than a command somebody ran once.

**Admission is one direction at a time, and ours says nothing about theirs.** A
pair that reads from each other needs `source` and `requester` on both sides —
four entries across the two deployments. The packs must be ones the other
network actually publishes; `oanfed` fetches their documents and refuses any
pack that is not there.

No key is needed. Admission reads their **public** documents and writes a row
in our registry; nothing here signs.

## What this chart does not do

It does not open a path for anything but the three documents. The server
answers 404 for everything else, and the Ingress template refuses a path
outside `/.well-known/`.

## Values worth knowing

| | |
|---|---|
| `networkId`, `registryUrl` | must match what the adapter uses — the descriptor is built from that registry entry |
| `keys.existingSecret.name` | the adapter's Secret, not a copy |
| `ingress.tls` | leave empty if the adapter's Ingress already holds the certificate for this hostname. cert-manager orders per Ingress and Kong terminates on SNI, so a second block is a second ACME order for one name |
| `pipInstall` | `oanfed` needs `cryptography`, installed at start. An air-gapped cluster should set an image that carries it and leave this empty |
