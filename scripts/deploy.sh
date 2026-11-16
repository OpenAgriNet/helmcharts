#!/usr/bin/env bash
#
# Deploy config/<env>/ with plain Helm, in services.yaml order.
#
#   ./scripts/deploy.sh dev                # every service
#   ./scripts/deploy.sh dev kong           # only the services named
#   ./scripts/deploy.sh dev --dry-run      # helm template, install nothing
#   ./scripts/deploy.sh prod --set snapshotRoleArn=<arn>
#
# --set name=value  override a value in global.yaml
# --no-wait         do not wait for each release to be ready
# -y                skip the confirmation
set -euo pipefail

cd "$(dirname "$0")/.."

die() { echo "ERROR: $1" >&2; exit 1; }
usage() { sed -n '3,12p' "$0" | cut -c3-; exit "${1:-0}"; }

environment=""
selected=()
overrides=()
dry_run=false
wait_flags=(--wait --timeout 10m)
assume_yes=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --set) [[ -n "${2:-}" ]] || die "--set needs name=value"
           overrides+=("$2"); shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --no-wait) wait_flags=(); shift ;;
    -y|--yes) assume_yes=true; shift ;;
    -h|--help) usage ;;
    -*) die "unknown option: $1" ;;
    *) if [[ -z "$environment" ]]; then environment="$1"
       else selected+=("$1"); fi
       shift ;;
  esac
done

[[ -n "$environment" ]] || usage 1
[[ -d "config/$environment" ]] || die "no such environment: $environment"
command -v helm >/dev/null || die "helm is not on PATH"

rendered="rendered/$environment"

# Helm cannot read one values file from another, so ${name} is filled in
# from global.yaml here. Writes rendered/<env>/<service>.yaml and prints
# one line per service: name chart namespace repository version release.
services="$(python3 - "config/$environment" "$rendered" "${overrides[@]}" <<'EOF'
import json
import pathlib
import re
import sys

import yaml

config, rendered = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
reference = re.compile(r"\$\{(\w+)\}")


def fail(message):
    sys.exit(f"ERROR: {message}")


def lookup(name, where):
    if name not in shared:
        fail(f"{where}: ${{{name}}} is not in global.yaml")
    if shared[name] is None:
        fail(f"{where}: ${{{name}}} is not set, pass --set {name}=...")
    return shared[name]


def as_text(name, where):
    value = lookup(name, where)
    if isinstance(value, (dict, list)):
        fail(f"{where}: ${{{name}}} is a map, use it as the whole value")
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def render(value, where):
    if isinstance(value, dict):
        return {k: render(v, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, where) for v in value]
    if not isinstance(value, str):
        return value
    whole = reference.fullmatch(value)
    if whole:  # keeps the type, so a map or a number works
        return lookup(whole.group(1), where)
    return reference.sub(lambda m: as_text(m.group(1), where), value)


shared = yaml.safe_load((config / "global.yaml").read_text()) or {}
for assignment in sys.argv[3:]:
    name, equals, value = assignment.partition("=")
    if not equals:
        fail(f"--set {assignment}: expected name=value")
    shared[name] = value

rendered.mkdir(parents=True, exist_ok=True)
services = yaml.safe_load((config / "services.yaml").read_text()) or {}
for name, service in services.items():
    if "chart" not in service or "namespace" not in service:
        fail(f"{name}: needs chart and namespace")
    values = render(service.get("values") or {}, name)
    # JSON quotes every string, so Helm cannot read "y" or "on" as a boolean.
    (rendered / f"{name}.yaml").write_text(json.dumps(values, indent=2))
    # "-" for no repository: read collapses empty fields.
    print(name, service["chart"], service["namespace"],
          service.get("repository", "-"), service.get("version", "-"),
          service.get("releaseName", name))
EOF
)"

for name in "${selected[@]}"; do
  grep -q "^$name " <<<"$services" || die "$name is not in services.yaml"
done

if [[ "$dry_run" == false && "$assume_yes" == false ]]; then
  context="$(kubectl config current-context 2>/dev/null || echo unknown)"
  echo -n "Deploy $environment to '$context'? [y/N] " >&2
  read -r answer || true
  [[ "${answer:-}" == [yY] ]] || die "aborted"
fi

while read -r name chart namespace repository version release; do
  [[ -n "$name" ]] || continue
  if [[ ${#selected[@]} -gt 0 && " ${selected[*]} " != *" $name "* ]]; then
    continue
  fi

  source=()
  if [[ "$repository" != - ]]; then
    source=(--repo "$repository" --version "$version")
  elif grep -q 'file://' "$chart/Chart.yaml"; then
    helm dependency build "$chart" >/dev/null
  fi
  values="$rendered/$name.yaml"

  if [[ "$dry_run" == true ]]; then
    helm template "$release" "$chart" "${source[@]}" -n "$namespace" \
      -f "$values" >/dev/null
    echo "ok   $name"
  else
    echo "-> $name"
    helm upgrade --install "$release" "$chart" "${source[@]}" \
      -n "$namespace" --create-namespace -f "$values" "${wait_flags[@]}"
  fi
done <<<"$services"
