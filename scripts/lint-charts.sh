#!/usr/bin/env bash
#
# Lint and render every chart under charts/.
#
# Run locally exactly as CI runs it:
#   ./scripts/lint-charts.sh
#
# Charts depending on oan-common via file://../oan-common carry no committed
# dependency artifact, so the dependency is rebuilt here before linting. That
# also means a local edit to oan-common is only picked up after this runs (or
# after `helm dependency update <chart>`).
#
# A chart whose defaults deliberately fail the render - because it requires a
# database host or a secret reference that has no safe default - supplies the
# minimum needed to template in ci/lint-values.yaml. Every file matching
# ci/*-values.yaml is rendered as a separate case, so a chart can cover several
# configurations.
set -euo pipefail

cd "$(dirname "$0")/.."

failed=0

for chart_dir in charts/*/; do
  chart_dir="${chart_dir%/}"
  [[ -f "$chart_dir/Chart.yaml" ]] || continue

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

if [[ "$failed" -ne 0 ]]; then
  echo "chart validation FAILED"
  exit 1
fi

echo "all charts linted and rendered successfully"
