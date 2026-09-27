#!/usr/bin/env bash
#
# One-time setup of a freshly installed OpenBao, so External Secrets Operator
# can read from it. Safe to re-run: every step checks before it acts.
#
#   ./scripts/openbao-configure.sh --env dev [--init-out ~/openbao-dev-init.json]
#
# What it does, in order:
#   1. Initialises OpenBao if it is not yet. With the static seal there are no
#      unseal keys, but init still returns RECOVERY keys and the ROOT token.
#      They are written to --init-out (0600) and printed nowhere else. Move that
#      file into the password manager and delete it -- it is the master key to
#      every credential OAN has.
#   2. Mounts KV v2 at secret/.
#   3. Enables Kubernetes auth, checking tokens against this cluster's API.
#   4. Writes policy oan-<env>-read: read on secret/data/oan/<env>/* and nothing
#      else.
#   5. Creates role external-secrets, bound to the external-secrets
#      ServiceAccount, with that policy.
#
# Needs kubectl pointed at the cluster, and jq.
set -euo pipefail

env=""
namespace="openbao"
pod="openbao-0"
init_out=""
eso_sa="external-secrets"
eso_ns="external-secrets"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env="$2"; shift 2 ;;
    --namespace) namespace="$2"; shift 2 ;;
    --pod) pod="$2"; shift 2 ;;
    --init-out) init_out="$2"; shift 2 ;;
    --eso-sa) eso_sa="$2"; shift 2 ;;
    --eso-namespace) eso_ns="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$env" ]] || { echo "--env is required (dev, staging, prod)" >&2; exit 2; }

bao() { kubectl -n "$namespace" exec -i "$pod" -- env BAO_TOKEN="${BAO_TOKEN:-}" bao "$@"; }

# --- 1. init -----------------------------------------------------------------
initialized="$(bao status -format=json 2>/dev/null | jq -r .initialized || true)"
if [[ "$initialized" != "true" ]]; then
  [[ -n "$init_out" ]] || { echo "OpenBao is not initialised; pass --init-out <file> to receive the root token and recovery keys" >&2; exit 2; }
  [[ ! -e "$init_out" ]] || { echo "$init_out already exists; refusing to overwrite what may be the only copy of a root token" >&2; exit 2; }
  echo "==> initialising OpenBao"
  (umask 077; bao operator init -format=json > "$init_out")
  echo "    root token and recovery keys written to $init_out -- move them off this machine"
fi

if [[ -z "${BAO_TOKEN:-}" ]]; then
  [[ -n "$init_out" && -f "$init_out" ]] || { echo "set BAO_TOKEN, or pass --init-out pointing at the init output" >&2; exit 2; }
  BAO_TOKEN="$(jq -r .root_token "$init_out")"
fi
export BAO_TOKEN

# The seal is static, so it should unseal on its own. Wait for it rather than
# failing on the first check straight after init.
for _ in $(seq 1 30); do
  [[ "$(bao status -format=json 2>/dev/null | jq -r .sealed)" == "false" ]] && break
  sleep 2
done
[[ "$(bao status -format=json 2>/dev/null | jq -r .sealed)" == "false" ]] || { echo "OpenBao is still sealed -- check the unseal key Secret is mounted" >&2; exit 1; }

# --- 2. KV v2 ----------------------------------------------------------------
if ! bao secrets list -format=json | jq -e '."secret/"' >/dev/null; then
  echo "==> mounting kv-v2 at secret/"
  bao secrets enable -path=secret kv-v2
fi

# --- 3. Kubernetes auth ------------------------------------------------------
if ! bao auth list -format=json | jq -e '."kubernetes/"' >/dev/null; then
  echo "==> enabling kubernetes auth"
  bao auth enable kubernetes
fi
# Inside the pod, OpenBao uses its own ServiceAccount token to call TokenReview,
# which is what the chart's authDelegator binding allows.
bao write auth/kubernetes/config kubernetes_host="https://kubernetes.default.svc:443" >/dev/null

# --- 4. policy ---------------------------------------------------------------
policy="oan-${env}-read"
echo "==> writing policy $policy"
bao policy write "$policy" - <<EOF
path "secret/data/oan/${env}/*" {
  capabilities = ["read"]
}
EOF

# --- 5. role -----------------------------------------------------------------
echo "==> writing role external-secrets"
bao write auth/kubernetes/role/external-secrets \
  bound_service_account_names="$eso_sa" \
  bound_service_account_namespaces="$eso_ns" \
  token_policies="$policy" \
  token_ttl=1h >/dev/null

echo "done. Write secrets with:"
echo "  kubectl -n $namespace exec -it $pod -- bao kv put secret/oan/${env}/<name> key=value ..."
