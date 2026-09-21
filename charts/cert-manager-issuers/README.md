# cert-manager-issuers

Let's Encrypt `ClusterIssuer`s for cert-manager. Renders no workload — two
custom resources and nothing else.

## Why it is not part of the cert-manager chart

A `ClusterIssuer` is an instance of a CRD that the cert-manager chart
registers. Helm renders a release's manifests in one pass, so the same release
cannot define a CRD and create an object of that kind: the API server rejects
the instance as an unknown kind because the definition is not established yet.

## Prerequisites, in order

1. **The CRDs, applied out of band.** Three exceed the 262144-byte cap on
   `metadata.annotations` that a client-side apply writes into
   `kubectl.kubernetes.io/last-applied-configuration`:

   | CRD | bytes |
   |---|---|
   | `clusterissuers` | 325618 |
   | `issuers` | 325487 |
   | `challenges` | 268992 |

   so `crds.enabled` stays `false` in the cert-manager chart — its own default
   — and they go on server-side:

   ```bash
   kubectl apply --server-side -f \
     https://github.com/cert-manager/cert-manager/releases/download/v1.21.2/cert-manager.crds.yaml
   ```

2. **cert-manager itself** — [`charts/cert-manager`](../cert-manager), the
   official chart committed unmodified.

3. **These issuers.**

## What HTTP-01 requires

Let's Encrypt fetches `http://<host>/.well-known/acme-challenge/<token>` over
**plain HTTP** from the public internet. So for every host being certified:

- a public DNS record resolving to the ingress controller's address
- port 80 reachable from the internet, not just 443
- an `Ingress` whose `ingressClassName` matches `ingressClassName` here

Miss any of those and orders fail with a connection error rather than anything
mentioning certificates.

`<ip>.sslip.io` satisfies the DNS requirement without owning a domain — it
resolves `anything.<ip>.sslip.io` to `<ip>`. It is on the Public Suffix List,
so each `<ip>.sslip.io` is its own registered domain for rate-limiting rather
than sharing one bucket with every other user.

## Using an issuer

Reference it by name from an Ingress:

```yaml
metadata:
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-staging
spec:
  tls:
    - hosts: [consumer.<ip>.sslip.io]
      secretName: consumer-adapter-tls
```

Get a certificate issued by `letsencrypt-staging` first. It is untrusted by
browsers on purpose, and proves DNS and port 80 work before production's rate
limits are at stake. Then switch the annotation to `letsencrypt-prod` and
delete the Secret so a new order runs.

## Values

| Key | Default | Purpose |
|---|---|---|
| `email` | `""` | Contact for expiry warnings, for issuers that set no `email` of their own |
| `ingressClassName` | `kong` | Ingress class that answers the challenge |
| `issuers.<name>.enabled` | `true` | Whether to render that issuer |
| `issuers.<name>.server` | — | ACME directory URL. Required |
| `issuers.<name>.email` | — | Overrides the top-level `email` |
| `issuers.<name>.privateKeySecretName` | `<name>-account-key` | Where cert-manager stores the ACME **account** key |

The map key is the `ClusterIssuer`'s name and is what an Ingress references, so
renaming one orphans every certificate pointing at it.
