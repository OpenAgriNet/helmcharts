# config

Two files per environment:

- `<env>/global.yaml`: values many services share (domain, URLs).
- `<env>/services.yaml`: every service in install order, with its values.

A value in `services.yaml` reads a global as `${name}`.
Change the domain once in `global.yaml` and every service follows.

## Deploy

```bash
./scripts/deploy.sh dev --dry-run   # helm template only
./scripts/deploy.sh dev             # install every service
./scripts/deploy.sh dev kong        # install only kong
```

- `${name}` used as the whole value keeps its type (a map stays a map).
- A global set to `null` must be passed with `--set name=value`.
- An unknown `${name}` stops the deploy before anything is installed.
- Rendered values go to `rendered/<env>/` (gitignored).

## New cluster

Not done by the script:

- Apply the cert-manager and CloudNativePG CRDs server side first.
- Deploy openbao with `--no-wait`: it is not ready until unsealed.
- Push secrets to OpenBao; write adapter key ids after registry-seed.

## VM (k3s)

Set these in `global.yaml`:

```yaml
storageClass: local-path
loadBalancerAnnotations: {}
```
