#!/usr/bin/env bash
#
# Turn one run directory into a report that stands on its own.
#
#   report.sh --run-dir results/publish-20260918-101500 --data-dir data/publish
#
# Reads what run-benchmark.sh left behind -- run.env, results.jtl, resources.csv -- and
# writes report.md. Nothing here talks to the network, so a report can be
# rebuilt from a finished run at any time.

set -euo pipefail

RUN_DIR=""
DATA_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --run-dir)  RUN_DIR="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

[ -n "$RUN_DIR" ] || { echo "--run-dir is required" >&2; exit 1; }

JTL="$RUN_DIR/results.jtl"
RESOURCES="$RUN_DIR/resources.csv"
REPORT="$RUN_DIR/report.md"

# run.env is written by run-benchmark.sh as key=value.
get() { sed -n "s/^$1=//p" "$RUN_DIR/run.env" 2>/dev/null | head -1; }

# `ended` is appended only when the load finishes, so a killed run has none.
# Saying so beats an empty cell that reads like a bug in the report.
ended() {
  local value
  value="$(get ended)"
  if [ -n "$value" ]; then echo "$value"; else echo "did not finish"; fi
}

# Column index by name, because the .jtl column order depends on which fields
# the plan saved -- reading by position is how a report quietly starts
# reporting the wrong number.
col() {
  head -1 "$JTL" | tr ',' '\n' | grep -nxF "$1" | cut -d: -f1
}

# One physical line per SAMPLE, whatever JMeter wrote.
#
# A .jtl is CSV, and JMeter puts the assertion failure straight into
# failureMessage -- which for a failed publish is several lines of
#
#     Test failed: code expected to equal /
#     ****** received  : [[[503]]]
#
# quoted as a single field. So a failed request occupies seven physical lines
# where a successful one occupies a single line.
#
# Counting lines therefore counts every failure about seven times, and each
# figure built on that count is wrong in the direction that FLATTERS the run:
# more requests in the same wall time reads as higher throughput, and the same
# failures over a larger denominator reads as a lower error rate. On the first
# 100k publish pass it turned 1,304 requests at 0.67/s with 43% errors into
# 4,676 requests at 2.45/s with 12% errors. It also dragged the percentiles
# down, by feeding 1,866 values into a set that holds 1,304.
#
# Records are rejoined on QUOTE PARITY rather than by matching a timestamp at
# the start of a line: a quoted field is closed only once the number of quote
# characters seen is even, which is the actual CSV rule. Doubled quotes inside
# a field ("") preserve that parity, so they need no special case.
records() {
  tail -n +2 "$JTL" | awk '
    {
      buf = (buf == "" ? $0 : buf " " $0)
      q += gsub(/"/, "\"")
      if (q % 2 == 0) { print buf; buf = ""; q = 0 }
    }
    END { if (buf != "") print buf }
  '
}

percentile() {
  local column="$1" p="$2"
  records | cut -d, -f"$column" | grep -E '^[0-9]+$' | sort -n \
    | awk -v p="$p" '{ v[NR] = $1 } END {
        if (NR == 0) { print "n/a"; exit }
        i = int((p / 100) * NR + 0.5); if (i < 1) i = 1; if (i > NR) i = NR
        print v[i]
      }'
}

# ------------------------------------------------------------- load figures

if [ -f "$JTL" ] && [ "$(wc -l < "$JTL")" -gt 1 ]; then
  C_ELAPSED=$(col elapsed)
  C_SUCCESS=$(col success)
  C_LATENCY=$(col Latency)
  C_TIMESTAMP=$(col timeStamp)

  SAMPLES=$(records | wc -l)
  FAILURES=$(records | cut -d, -f"$C_SUCCESS" | grep -cx "false" || true)
  ERROR_RATE=$(awk -v f="$FAILURES" -v n="$SAMPLES" 'BEGIN { printf "%.2f", n ? (f / n) * 100 : 0 }')

  WALL=$(records | cut -d, -f"$C_TIMESTAMP" | grep -E '^[0-9]+$' | sort -n \
    | awk 'NR == 1 { first = $1 } { last = $1 } END { printf "%.1f", (last - first) / 1000 }')
  THROUGHPUT=$(awk -v n="$SAMPLES" -v w="$WALL" 'BEGIN { printf "%.2f", (w > 0) ? n / w : 0 }')

  E50=$(percentile "$C_ELAPSED" 50);  E90=$(percentile "$C_ELAPSED" 90)
  E95=$(percentile "$C_ELAPSED" 95);  E99=$(percentile "$C_ELAPSED" 99)
  L95=$(percentile "$C_LATENCY" 95)
else
  SAMPLES=0; FAILURES=0; ERROR_RATE="n/a"; WALL=0; THROUGHPUT="n/a"
  E50="n/a"; E90="n/a"; E95="n/a"; E99="n/a"; L95="n/a"
fi

# --------------------------------------------------------- resource figures

resource_rows() {
  [ -f "$RESOURCES" ] || return 0
  tail -n +2 "$RESOURCES" | awk -F, '
    {
      name = $2; cpu = $3; mem = $4
      cpu_sum[name] += cpu; mem_sum[name] += mem; count[name]++
      if (cpu > cpu_max[name]) cpu_max[name] = cpu
      if (mem > mem_max[name]) mem_max[name] = mem
    }
    END {
      for (n in count)
        printf "| %s | %d | %d | %.0f | %.0f |\n", n,
          cpu_sum[n] / count[n], cpu_max[n],
          (mem_sum[n] / count[n]) / 1048576, mem_max[n] / 1048576
    }' | sort
}

# ------------------------------------------------------------------ payloads

payloads_line="not recorded"
payloads_states=""
if [ -n "$DATA_DIR" ] && [ -f "$DATA_DIR/manifest.json" ]; then
  payloads_line=$(python3 - "$DATA_DIR/manifest.json" <<'PY' 2>/dev/null || echo "not readable"
import json, sys
m = json.load(open(sys.argv[1]))
scale = m.get("scaleFactor", 1)
suffix = "" if scale == 1 else f" ({m['realMarkets']} real markets x {scale})"
print(f"{m['resources']} resources in {m['catalogs']} catalogs{suffix}, "
      f"{m['payloads']} publish payload(s)")
PY
)
  payloads_states=$(python3 - "$DATA_DIR/manifest.json" <<'PY' 2>/dev/null || true
import json, sys
m = json.load(open(sys.argv[1]))
for s in m.get("states", []):
    print(f"| {s['code']} | {s['name']} | {s['markets']} | {len(s.get('districts', []))} |")
PY
)
fi

# ------------------------------------------------------------------ write

{
  echo "# Benchmark report — $(get capability) $(get scenario)"
  echo
  [ -n "$(get note)" ] && { echo "$(get note)"; echo; }
  echo "## What this run was"
  echo
  echo "| | |"
  echo "|---|---|"
  echo "| Tool | $(get tool) |"
  echo "| Scenario | $(get scenario) |"
  echo "| Capability | $(get capability) |"
  echo "| Started | \`$(get started)\` |"
  echo "| Ended | \`$(ended)\` |"
  echo "| Configuration | $(get config) |"
  echo "| Target | \`$(get host):$(get port)$(get path)\` |"
  echo "| Baseline round trip | $(get baseline) |"
  if [ "$(get scheduler)" = "false" ]; then
    echo "| Load | $(get threads) threads, ramp-up $(get rampUp)s, $(get loops) loops each |"
  else
    echo "| Load | $(get threads) threads, ramp-up $(get rampUp)s, duration $(get duration)s |"
  fi
  if [ "$(get ratePerMin)" = "0" ]; then
    echo "| Target rate | none — threads run flat out |"
  else
    echo "| Target rate | $(get ratePerMin) requests/minute |"
  fi
  echo "| Payloads | $payloads_line |"
  echo "| Load generator | $(uname -s) $(uname -m), $(nproc 2>/dev/null || echo '?') vCPU |"
  echo

  if [ -n "$(get limits)" ] || [ -n "$(get observedLimits)" ]; then
    echo "### What each service was given"
    echo
    echo "| Service | As labelled | As deployed |"
    echo "|---|---|---|"
    printf '%s\n' "$(get limits)" | tr ';' '\n' | while read -r entry; do
      entry="$(echo "$entry" | sed 's/^ *//')"
      [ -n "$entry" ] || continue
      name="${entry%%=*}"
      value="${entry#*=}"
      deployed="$(printf '%s\n' "$(get observedLimits)" | tr ';' '\n' \
        | sed -n "s/^ *$name //p" | head -1)"
      echo "| $name | $value | ${deployed:-—} |"
    done
    echo
    echo "Labels say what was dialled in Helm. \"As deployed\" is what the pods"
    echo "actually report, read from the cluster during the run."
    echo
  fi

  if [ -n "$payloads_states" ]; then
    echo "### Published data"
    echo
    echo "| State | Name | Markets | Districts |"
    echo "|---|---|---|---|"
    echo "$payloads_states"
    echo
  fi

  echo "## Results"
  echo
  echo "| Measure | Value |"
  echo "|---|---|"
  echo "| Requests | $SAMPLES |"
  echo "| Failures | $FAILURES |"
  echo "| Error rate | $ERROR_RATE % |"
  echo "| Wall time | $WALL s |"
  echo "| Throughput | $THROUGHPUT requests/second |"
  echo "| Response time p50 | $E50 ms |"
  echo "| Response time p90 | $E90 ms |"
  echo "| Response time p95 | $E95 ms |"
  echo "| Response time p99 | $E99 ms |"
  echo "| Time to first byte p95 | $L95 ms |"
  echo
  echo "Response time is the whole exchange as seen from the load generator, so"
  echo "it includes the network. Time to first byte excludes the response"
  echo "download; comparing the two against the baseline above separates a slow"
  echo "service from a distant one."
  echo

  echo "## CPU and memory"
  echo
  if [ -f "$RESOURCES" ] && [ "$(wc -l < "$RESOURCES")" -gt 1 ]; then
    echo "| Component | CPU mean (millicores) | CPU peak | Memory mean (MiB) | Memory peak |"
    echo "|---|---|---|---|---|"
    resource_rows
    echo
    echo "1000 millicores is one full core. Peak is the highest value observed"
    echo "at a $(get sampleInterval)-second sampling interval, not the highest reached — a"
    echo "shorter spike falls between samples."
  else
    echo "Not collected. Re-run with \`--sample-source docker\` or \`--sample-source k8s\`."
  fi
  echo

  echo "## Going back to this run"
  echo
  echo "Search a dashboard for this window:"
  echo
  echo "\`\`\`"
  echo "from  $(get started)"
  echo "to    $(ended)"
  echo "\`\`\`"
  echo
  echo "Both are UTC. The CPU and memory table above is sampled by this harness"
  echo "and is complete on its own — the dashboard is for the things the harness"
  echo "does not collect, such as disk and network I/O."
  echo

  echo "## Files"
  echo
  echo "| File | Holds |"
  echo "|---|---|"
  echo "| \`results.jtl\` | every request, as JMeter recorded it |"
  echo "| \`resources.csv\` | CPU and memory samples |"
  echo "| \`progress.log\` | the running summary printed during the run |"
  echo "| \`html/index.html\` | JMeter's own dashboard, with charts over time |"
  echo "| \`run.env\` | the settings this run used |"
} > "$REPORT"

echo "report: $REPORT"
