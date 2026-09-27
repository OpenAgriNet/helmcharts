#!/usr/bin/env bash
#
# Copy every OAN secret from AWS Secrets Manager into OpenBao, value for value.
#
#   BAO_TOKEN=... ./scripts/asm-to-openbao.sh --env dev [--dry-run]
#
# COPY, DO NOT REGENERATE. The values already in use are baked into things that
# outlive the Secret: Postgres role passwords, the admin-api client secret
# Keycloak stored at realm import, and the adapter signing keys whose public
# halves are registered in the registry. A regenerated value is a different
# value, and each of those fails in its own way.
#
# Reads oan/<env>/<name> from Secrets Manager for every name in oan-secrets'
# values, and writes the same JSON object to secret/oan/<env>/<name> in
# OpenBao. Then compare with scripts/secrets-snapshot.sh before and after the
# switch.
#
# Needs the aws CLI with read access to the entries, kubectl pointed at the
# cluster, and a BAO_TOKEN that can write under secret/oan/<env>/ (the ESO
# policy is read-only, deliberately; use the root token or an admin one).
set -euo pipefail

cd "$(dirname "$0")/.."

env=""
namespace="openbao"
pod="openbao-0"
dry_run=false
values="charts/oan-secrets/values.yaml"
aws_cli="${AWS_CLI:-aws}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env="$2"; shift 2 ;;
    --namespace) namespace="$2"; shift 2 ;;
    --pod) pod="$2"; shift 2 ;;
    --values) values="$2"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$env" ]] || { echo "--env is required (dev, staging, prod)" >&2; exit 2; }
$dry_run || [[ -n "${BAO_TOKEN:-}" ]] || { echo "BAO_TOKEN is required" >&2; exit 2; }

names="$(sed -n 's/^[[:space:]]*- name:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$values")"

# The aws CLI's stderr goes here, not into the value: a warning it prints on a
# successful call would otherwise be prepended to the JSON and fail the check
# below for every entry.
aws_err="$(mktemp)"
trap 'rm -f "$aws_err"' EXIT

failed=0
for name in $names; do
  key="oan/${env}/${name}"
  if ! value="$($aws_cli secretsmanager get-secret-value --secret-id "$key" --query SecretString --output text 2>"$aws_err")"; then
    echo "!! $key: could not read from Secrets Manager: $(cat "$aws_err")" >&2
    failed=1
    continue
  fi
  # Must be a JSON object: ESO's dataFrom.extract turns its keys into the
  # Secret's keys, on either store.
  if ! jq -e 'type == "object"' >/dev/null 2>&1 <<<"$value"; then
    echo "!! $key: not a JSON object, skipped" >&2
    failed=1
    continue
  fi
  if $dry_run; then
    echo "would copy $key ($(jq -r 'keys | join(", ")' <<<"$value"))"
    continue
  fi
  # Through stdin, so no value appears in a process list or in shell history.
  # Guarded, so one failed write -- a bad token, no write access, a restarting
  # pod -- is reported and the rest still copy, instead of set -e stopping the
  # run halfway with no record of which entries made it.
  if ! kubectl -n "$namespace" exec -i "$pod" -- env BAO_TOKEN="$BAO_TOKEN" \
      bao kv put "secret/${key}" - <<<"$value" >/dev/null; then
    echo "!! $key: write to OpenBao failed" >&2
    failed=1
    continue
  fi
  echo "copied $key"
done

exit "$failed"
