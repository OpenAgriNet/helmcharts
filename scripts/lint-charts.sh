#!/usr/bin/env bash
#
# Lint and render every chart under charts/, at the top level (charts/<name>)
# or grouped one directory deeper (charts/<group>/<name>, e.g.
# charts/knowledge-provider/knowledge-provider-api) - anywhere a Chart.yaml is
# found up to two directories below charts/.
#
# A chart lives at charts/<chart>, or one level down at charts/<group>/<chart>
# when it belongs to a group of related charts (charts/dss/model-gateway). A
# group directory has no Chart.yaml of its own.
#
# Run locally exactly as CI runs it:
#   ./scripts/lint-charts.sh
#
# Charts depending on common via file://../common (or file://../../common one
# directory deeper) carry no committed dependency artifact, so the dependency
# is rebuilt here before linting. That also means a local edit to common is
# only picked up after this runs (or after `helm dependency update <chart>`).
#
# A chart whose defaults deliberately fail the render - because it requires a
# database host or a secret reference that has no safe default - supplies the
# minimum needed to template in ci/lint-values.yaml. Every file matching
# ci/*-values.yaml is rendered as a separate case, so a chart can cover several
# configurations.
set -euo pipefail

cd "$(dirname "$0")/.."

failed=0

# Collect charts/<chart> and charts/<group>/<chart>. A chart's own charts/
# directory (vendored dependencies) is never descended into.
chart_dirs=()
for dir in charts/*/; do
  dir="${dir%/}"
  if [[ -f "$dir/Chart.yaml" ]]; then
    chart_dirs+=("$dir")
    continue
  fi
  for sub in "$dir"/*/; do
    sub="${sub%/}"
    [[ -f "$sub/Chart.yaml" ]] && chart_dirs+=("$sub")
  done
done

for chart_dir in "${chart_dirs[@]}"; do

  name="$(basename "$chart_dir")"
  chart_type="$(helm show chart "$chart_dir" 2>/dev/null | awk '/^type:/ {print $2}')"
  chart_type="${chart_type:-application}"

  echo "==> $name (type: $chart_type)"

  if grep -q '^dependencies:' "$chart_dir/Chart.yaml"; then
    helm dependency update "$chart_dir" >/dev/null
  fi

  # Render once per ci/*-values.yaml, or once with plain defaults if there are none.
  shopt -s nullglob
  ci_values=("$chart_dir"/ci/*-values.yaml)
  shopt -u nullglob

  # Pass the same values to lint. Without them, lint renders bare defaults and
  # reports the chart's own "this value is required" failures as [INFO] Fail
  # lines - noise that looks like a broken chart but is the guardrail working.
  # Written this way for bash 3.2 (macOS), where "${empty_array[@]}" under
  # `set -u` is an unbound-variable error rather than an empty expansion.
  lint_args=()
  if [[ ${#ci_values[@]} -gt 0 ]]; then
    for values in "${ci_values[@]}"; do
      lint_args+=(-f "$values")
    done
  fi

  if ! helm lint --strict "$chart_dir" "${lint_args[@]+"${lint_args[@]}"}"; then
    echo "!!! helm lint failed for $name"
    failed=1
    continue
  fi

  # Library charts render no resources of their own, so there is nothing to template.
  if [[ "$chart_type" == "library" ]]; then
    continue
  fi

  if [[ ${#ci_values[@]} -eq 0 ]]; then
    if ! helm template "$name" "$chart_dir" >/dev/null; then
      echo "!!! helm template failed for $name"
      failed=1
    fi
  else
    for values in "${ci_values[@]}"; do
      echo "    render: $(basename "$values")"
      if ! helm template "$name" "$chart_dir" -f "$values" >/dev/null; then
        echo "!!! helm template failed for $name with $(basename "$values")"
        failed=1
      fi
    done
  fi
done

# Grafana dashboards and alerting exist twice: in charts/grafana-provisioning
# for the cluster, and in the quick-start's config/grafana for Compose. They
# must be the same set of files with the same contents, or a dashboard or alert
# changed -- or added -- in one path silently differs in the other. So both
# sides are listed, and a file on either side without a twin fails too.
#
# infra-overview.json is the exception: the cluster copy reads kubelet metrics,
# the quick-start copy Docker container stats, so the two differ by design.
echo "==> grafana provisioning: chart and quick-start copies"
chart=charts/grafana-provisioning
qs=quick-start/config/grafana
platform_specific_dashboard=infra-overview.json
# nullglob: a pattern matching nothing expands to nothing, not to itself.
shopt -s nullglob
for pair in "$chart/dashboards:$qs/provisioning/dashboards/json:json" \
            "$chart/alerting:$qs/alerting-true:yaml"; do
  IFS=: read -r chart_dir qs_dir ext <<<"$pair"
  names=$( { for file in "$chart_dir"/*."$ext" "$qs_dir"/*."$ext"; do basename "$file"; done; } | sort -u)
  for name in $names; do
    if [[ ! -f "$chart_dir/$name" || ! -f "$qs_dir/$name" ]]; then
      echo "!!! $name is in only one of $chart_dir and $qs_dir -- add it to the other"
      failed=1
    elif [[ "$name" != "$platform_specific_dashboard" ]] && ! cmp -s "$chart_dir/$name" "$qs_dir/$name"; then
      echo "!!! $chart_dir/$name and $qs_dir/$name differ -- copy one over the other"
      failed=1
    fi
  done
done
shopt -u nullglob

if [[ "$failed" -ne 0 ]]; then
  echo "chart validation FAILED"
  exit 1
fi

echo "all charts linted and rendered successfully"
