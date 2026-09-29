# OpenBao, locally

The OAN secret store in Docker Compose, for trying it out and for testing
against without a cluster. It runs the same server as the Helm chart
([`charts/openbao`](../../charts/openbao), `examples/openbao.dev.yaml`): the
same image (OpenBao 2.6.3), raft storage, the static seal, and a file audit
log. Secrets use the same paths too, `secret/oan/<env>/<name>`.

It runs on its own. The quick-start stack next door still reads its
credentials from `.env`, not from here.

## Start it

Needs Docker with the compose plugin, and `jq`.

```bash
cd quick-start/openbao
bin/openbao.sh up      # start it. On the first run: init, then mount KV v2 at secret/
bin/openbao.sh seed    # load sample-values.json into secret/oan/dev/
```

The first `up` writes the **root token and recovery keys** to `init.json`
(mode 0600, gitignored). That file is the only copy: without it, the only way
back in is `destroy`.

Then open the UI at <http://127.0.0.1:8200/ui> and sign in with the token:

```bash
jq -r .root_token init.json
```

Or use the CLI inside the container:

```bash
export BAO_TOKEN=$(jq -r .root_token init.json)
docker compose exec -e BAO_TOKEN openbao bao kv get secret/oan/dev/registry-db
docker compose exec -e BAO_TOKEN openbao bao kv put secret/oan/dev/registry-db username=registry password=...
```

## Stop it

| Command | What it does |
|---|---|
| `bin/openbao.sh down` | Stops it and **keeps** the data. The next `up` finds everything where it was. |
| `bin/openbao.sh destroy` | Stops it and **deletes** the data, the audit log and the unseal key. It asks first. |
| `bin/openbao.sh status` | Shows the container, and whether it is initialised and sealed. |

## Unsealing

You don't need to do anything. OpenBao starts sealed, and the **static seal**
unseals it with a 32-byte key read from a file, on every start. A restart, a
`down` then `up`, or a reboot all come back unsealed.

The `unseal-key` service creates that key the first time, in its own Docker
volume (`oan-openbao_openbao-unseal`), and never replaces it. A new key can't
open data sealed with the old one.

**Init is a one-time step**, and `up` does it for you. It creates the root
token and the recovery keys. With this seal, the recovery keys aren't for
unsealing; they're for operations like generating a new root token.

To keep a copy of the unseal key with `init.json`:

```bash
bin/openbao.sh backup-key ~/openbao-local-unseal.key
```

## Sample secrets

[`sample-values.json`](sample-values.json) has one entry for each secret
in [`charts/oan-secrets`](../../charts/oan-secrets/values.yaml), with the key
names the charts read (`password`, `keycloakAdminClientSecret`,
`signingPrivateKey`, ...). Every value is a public placeholder.

```bash
bin/openbao.sh seed                  # secret/oan/dev/*
bin/openbao.sh seed --env staging    # secret/oan/staging/*
bin/openbao.sh seed --force          # overwrite entries that already exist
bin/openbao.sh seed --file mine.json # your own values, same format
```

Without `--force`, entries that already exist are skipped, so a second run
doesn't overwrite a real value with a placeholder.

## Checking that it works

| Check | Command | Expect |
|---|---|---|
| Up and healthy | `docker compose ps` | `openbao ... (healthy)` |
| Unsealed | `docker compose exec openbao bao status` | `Seal Type static`, `Sealed false` |
| Unseals on restart | `docker restart openbao`, then `bao status` again | `Sealed false`, with no one unsealing it |
| Data persists | `bin/openbao.sh down && bin/openbao.sh up`, then `kv get` | the same values |
| Audit log on | `docker compose exec -e BAO_TOKEN openbao bao audit list` | `file/` |
| Values not in the log | `docker compose exec openbao grep -c sample- /openbao/logs/audit.log` | `0`; values are HMAC'd |

## What is here

```
docker-compose.yml     unseal-key (one-shot) and openbao
config/openbao.hcl     the server config; keep it in step with the chart's
bin/openbao.sh         up, seed, status, backup-key, down, destroy
sample-values.json    placeholder values for every OAN secret
```

| Volume | Holds |
|---|---|
| `oan-openbao_openbao-data` | raft storage, every secret (encrypted) |
| `oan-openbao_openbao-audit` | `audit.log` |
| `oan-openbao_openbao-unseal` | `unseal.key` |

## Not for production

TLS is off and the port is published on `127.0.0.1` only. The root token sits
in a file on disk, and there is one node. For a real deployment, use the Helm
chart.
