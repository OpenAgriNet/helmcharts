#!/usr/bin/env bash
#
# Bring an environment up through Argo CD, and do the steps Argo CD cannot.
#
#   ./scripts/deploy.sh dev                 # everything below, in order
#   ./scripts/deploy.sh dev --skip-seed     # stop after pushing secrets
#   ./scripts/deploy.sh dev --dry-run       # show the Applications, touch nothing
#
#   1. checks    the kubectl context (confirmed), Argo CD, cert-manager and CNPG CRDs
#   2. apply     render config/ with config/<env>/argocd.yaml and apply one Argo CD
#                Application per service; drop the ones no longer rendered
#   3. openbao   secrets.sh setup: unseal key, init, configure (skipped once done)
#   4. secrets   no file: generate it (empty store) or pull it (store has values);
#                then push whatever changed
#   5. seed      wait for registry-seed, then secrets.sh key-ids (keyId, push, restart)
#
# config/<env>/ is local (gitignored), so it reaches the cluster only through
# this script: re-run it after changing it. Safe to re-run: every step checks
# before it acts. Chart changes still deploy by merge, at argocd.targetRevision.
#
# Options: -y (no confirmation)  --skip-secrets  --skip-seed  --timeout SECONDS
set -euo pipefail

cd "$(dirname "$0")/.."

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo; echo "==> $*"; }
usage() { sed -n '3,19p' "$0" | cut -c3-; exit "${1:-0}"; }

environment="" assume_yes=false dry_run=false skip_secrets=false skip_seed=false timeout=1800
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) assume_yes=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --skip-secrets) skip_secrets=true; shift ;;
    --skip-seed) skip_seed=true; shift ;;
    --timeout) timeout="${2:?--timeout needs seconds}"; shift 2 ;;
    -h|--help) usage ;;
    -*) die "unknown option: $1" ;;
    *) [[ -z "$environment" ]] || die "one environment only"; environment="$1"; shift ;;
  esac
done
[[ -n "$environment" ]] || usage 1
settings="config/$environment/argocd.yaml"
[[ -f "$settings" ]] || die "no $settings (copy config/example to config/$environment)"
for cmd in kubectl yq helm; do command -v "$cmd" >/dev/null || die "$cmd is not on PATH"; done
secrets() { ./scripts/secrets.sh "$environment" "$@"; }
app_on() { [[ "$(A="$1" yq '.applications[strenv(A)] // true' "$settings")" != false ]]; }
revision="$(yq '.argocd.targetRevision // "main"' "$settings")"

# The Applications, rendered from the local config/<env>/ files.
rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT
helm template oan config -f "$settings" > "$rendered" || die "config/$environment does not render (see above)"

if [[ "$dry_run" == true ]]; then
  yq 'select(.kind == "Application") | .metadata.annotations["argocd.argoproj.io/sync-wave"] + "  " + .metadata.name' "$rendered" \
    | grep -v '^---'
  echo "(dry run: the Applications Argo CD would create for $environment; nothing applied)"
  exit 0
fi

# ---------------------------------------------------------------- 1. checks
context="$(kubectl config current-context 2>/dev/null)" || die "no current kubectl context"
echo "environment  $environment"
echo "context      $context"
echo "revision     $revision"
if [[ "$assume_yes" == false ]]; then
  read -rp "Deploy $environment to this context? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted"
fi

kubectl get crd applications.argoproj.io >/dev/null 2>&1 \
  || die "Argo CD is not installed on this cluster (no applications.argoproj.io CRD)"
# Too large for client-side apply, so applied once, server side, outside Argo CD.
kubectl get crd certificates.cert-manager.io >/dev/null 2>&1 \
  || die "cert-manager CRDs are missing. Apply once:
  kubectl apply --server-side -f https://github.com/cert-manager/cert-manager/releases/download/v1.21.2/cert-manager.crds.yaml"
kubectl get crd clusters.postgresql.cnpg.io >/dev/null 2>&1 \
  || die "CloudNativePG CRDs are missing. Apply once, server side, from the cloudnative-pg chart version in config/$environment/service-values.yaml"
if app_on langfuse && ! kubectl get crd clickhouseclusters.clickhouse.com >/dev/null 2>&1; then
  die "ClickHouse operator CRDs are missing (langfuse needs them). Apply once:
  helm template ch charts/langfuse/charts/clickhouse-operator-helm \\
    --show-only templates/crd/clickhouseclusters.clickhouse.com.yaml \\
    --show-only templates/crd/keeperclusters.clickhouse.com.yaml | kubectl apply --server-side -f -"
fi

# ----------------------------------------------------------------- 2. apply
# --prune deletes this environment's Applications that are no longer rendered
# (switched off or removed); their finalizer then deletes what they created.
info "applying $(grep -c '^kind: Application' "$rendered") Applications"
kubectl apply -n argocd -f "$rendered" --prune \
  -l "app.kubernetes.io/managed-by=oan-config,oan.in/environment=$environment" \
  --prune-allowlist=argoproj.io/v1alpha1/Application

# ---------------------------------------------------------------- 3. openbao
if [[ "$skip_secrets" == false ]]; then
  app_on openbao || die "openbao is off in $settings; secrets cannot be stored (--skip-secrets to skip)"
  info "waiting for Argo CD to create the openbao Application"
  for _ in $(seq 1 60); do
    kubectl -n argocd get application openbao >/dev/null 2>&1 && break
    sleep 5
  done
  kubectl -n argocd get application openbao >/dev/null 2>&1 \
    || die "no openbao Application after 5 minutes: kubectl -n argocd get applications"

  info "OpenBao"
  secrets setup

  # ------------------------------------------------------------- 4. secrets
  info "secrets"
  if [[ ! -f "secrets/secrets-$environment.yaml" ]]; then
    if secrets list 2>&1 | grep -q '^-> nothing under'; then
      echo "empty store: generating a new secrets file"
      secrets generate
    else
      echo "the store already has values: pulling them (never regenerated over a live store)"
      secrets pull
    fi
  fi
  secrets push
  kubectl -n external-secrets annotate externalsecret --all "force-sync=$(date +%s)" --overwrite >/dev/null 2>&1 || true
fi

# ------------------------------------------------------------------- 5. seed
if [[ "$skip_seed" == false && "$skip_secrets" == false ]]; then
  if app_on registry-seed; then
    info "waiting for registry-seed (up to ${timeout}s); the registry and its database come first"
    for _ in $(seq 1 $((timeout / 10))); do
      kubectl -n registry get jobs -l app.kubernetes.io/name=registry-seed -o name 2>/dev/null | grep -q . && break
      sleep 10
    done
    secrets key-ids --timeout "$timeout"
  else
    echo "registry-seed is off in $settings; no keyId to resolve"
  fi
fi

cat <<EOF

Done. Argo CD keeps each service in step with its chart at $revision. After
changing config/$environment/, run this again to apply it.

  kubectl -n argocd get applications

If this run created them, move these into the password manager and delete them here:
  ~/openbao-$environment-unseal.key   the unseal key
  ~/openbao-$environment-init.json    the root token and recovery keys
EOF
