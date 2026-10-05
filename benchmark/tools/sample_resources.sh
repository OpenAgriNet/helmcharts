#!/usr/bin/env bash
#
# Sample CPU and memory of the services under test, until killed.
#
# No load generator reports this -- k6 and JMeter only know what the client saw
# -- so it is collected alongside the run and joined afterwards on time.
#
#   sample_resources.sh --source docker --names adapter,discovery --out r.csv
#   sample_resources.sh --source k8s --namespace oan --out r.csv
#
# --selector NARROWS the sampling. Leave it off and every pod in the namespace
# is sampled, which is usually what a benchmark wants: the request path runs
# through several adapters, discovery and two databases, and sizing any one of
# them means seeing all of them.
#
# Output columns are the same whichever source is used, so one report format
# works everywhere:
#
#   timestamp,name,cpu_millicores,mem_bytes
#
# CPU is normalised to millicores: 1000 means one full core. Docker reports a
# percentage where 100% is one core, Kubernetes reports millicores directly.
#
# --interval is the PAUSE between samples, not the period. Both `docker stats`
# and `kubectl top` take a second or more to answer, so asking for 2 gives
# samples roughly 5 seconds apart. That matters for a peak: a spike shorter than
# the real period is not sampled at all, so treat peak as "highest observed",
# not "highest reached".

set -euo pipefail

SOURCE=""
NAMES=""
NAMESPACE=""
SELECTOR=""
INTERVAL=2
OUT=""

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --source)    SOURCE="$2"; shift 2 ;;
    --names)     NAMES="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --selector)  SELECTOR="$2"; shift 2 ;;
    --interval)  INTERVAL="$2"; shift 2 ;;
    --out)       OUT="$2"; shift 2 ;;
    -h|--help)   usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

[ -n "$SOURCE" ] || { echo "--source is required (docker or k8s)" >&2; exit 1; }
[ -n "$OUT" ] || { echo "--out is required" >&2; exit 1; }

echo "timestamp,name,cpu_millicores,mem_bytes" > "$OUT"

# Turns "45.6MiB", "1.2GiB", "512Mi", "1Gi", "800KiB" into bytes.
to_bytes() {
  awk -v value="$1" '
    BEGIN {
      unit = value
      gsub(/[0-9.]/, "", unit)
      number = value
      gsub(/[^0-9.]/, "", number)
      multiplier = 1
      if (unit ~ /^KiB?$/) multiplier = 1024
      else if (unit ~ /^MiB?$/) multiplier = 1024 * 1024
      else if (unit ~ /^GiB?$/) multiplier = 1024 * 1024 * 1024
      else if (unit ~ /^kB$/) multiplier = 1000
      else if (unit ~ /^MB$/) multiplier = 1000 * 1000
      else if (unit ~ /^GB$/) multiplier = 1000 * 1000 * 1000
      printf "%d", number * multiplier
    }'
}

sample_docker() {
  local stamp names_filter
  stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # --no-stream takes one snapshot and exits, which is what makes this loop
  # rather than docker's own streaming mode: streaming emits on its own
  # schedule and cannot be joined to a fixed interval.
  docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' \
    | while IFS=$'\t' read -r name cpu mem; do
        if [ -n "$NAMES" ]; then
          echo "$NAMES" | tr ',' '\n' | grep -qxF "$name" || continue
        fi
        local cpu_m mem_value mem_bytes
        cpu_m=$(awk -v c="${cpu%\%}" 'BEGIN { printf "%d", c * 10 }')
        mem_value=${mem%% /*}
        mem_bytes=$(to_bytes "$mem_value")
        echo "$stamp,$name,$cpu_m,$mem_bytes" >> "$OUT"
      done
}

sample_k8s() {
  local stamp args
  stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  args=(top pod --no-headers)
  if [ -n "$NAMESPACE" ]; then
    args+=(--namespace "$NAMESPACE")
  else
    # EVERY namespace, not the current one.
    #
    # `kubectl top pod` with no namespace reads whatever the kubeconfig's
    # context happens to point at -- usually `default`, which holds nothing.
    # The samples file would come out with a header and no rows, and the report
    # would show an empty CPU table for a run that was fine.
    #
    # It matters here more than most: this deployment puts each component in
    # its own namespace -- consumer-adapter, network-adapter, provider-adapter,
    # discovery, registry, postgres -- so no single namespace covers a request
    # path.
    args+=(--all-namespaces)
  fi
  [ -n "$SELECTOR" ] && args+=(--selector "$SELECTOR")

  kubectl "${args[@]}" 2>/dev/null | while read -r c1 c2 c3 c4; do
    local name cpu mem cpu_m mem_bytes
    if [ -n "$NAMESPACE" ]; then
      # name cpu mem
      name=$c1; cpu=$c2; mem=$c3
    else
      # --all-namespaces prepends the namespace, so every field shifts one
      # right. Reading the old three would have taken the namespace as the pod
      # name and the pod name as the CPU figure -- garbage that still parses.
      #
      # Recorded as namespace/pod, which is also what makes the report
      # readable when two namespaces hold a pod of the same name.
      name="$c1/$c2"; cpu=$c3; mem=$c4
    fi
    cpu_m=${cpu%m}
    mem_bytes=$(to_bytes "$mem")
    echo "$stamp,$name,$cpu_m,$mem_bytes" >> "$OUT"
  done
}

case "$SOURCE" in
  docker) command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; } ;;
  k8s)    command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; } ;;
  *)      echo "--source must be docker or k8s, got $SOURCE" >&2; exit 1 ;;
esac

trap 'exit 0' TERM INT

while true; do
  case "$SOURCE" in
    docker) sample_docker ;;
    k8s)    sample_k8s ;;
  esac
  sleep "$INTERVAL"
done
