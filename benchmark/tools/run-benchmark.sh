#!/usr/bin/env bash
#
# Run one benchmark: drive load with JMeter, sample CPU and memory alongside it,
# and write a report that stands on its own.
#
#   ./run-benchmark.sh --scenario publish --url http://host:8080 \
#              --threads 20 --duration 600 \
#              --sample-source k8s --namespace oan
#
# Nothing about your environment is stored in this repo. The target URL and the
# pod or container names are arguments, and the report redacts the hostname
# unless you ask for it -- a report gets pasted into issues.
#
# Run ./run-benchmark.sh --help for the full list.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

SCENARIO="publish"
CAPABILITY="MandiPrice"
CONFIG="na"
LIMITS=""
URL=""
DATA_DIR=""
THREADS=10
RAMP_UP=30
DURATION=300
RATE_PER_MIN=0
LOOPS=-1
STARTUP_DELAY=0
ON_SAMPLE_ERROR=continue
CONNECT_TIMEOUT=10000
RESPONSE_TIMEOUT=120000
SAMPLE_SOURCE=""
NAMESPACE=""
SELECTOR=""
NAMES=""
SAMPLE_INTERVAL=5
HEALTH_PATH=""
RECORD_HOST="no"
OUT_ROOT="$ROOT/results"
NOTE=""

# JMeter prints a running summary on this interval. Five minutes by default, so
# a long run reports progress without burying it.
PROGRESS_INTERVAL=300

usage() {
  cat <<'EOF'
Usage: ./run-benchmark.sh [options]

  --scenario NAME       publish | discover | select. Chooses tools/jmeter-jmx/NAME.jmx
  --capability NAME     which capability's data to use (default MandiPrice).
                        Sets the payloads to data/<capability>-<scenario>
  --url URL             target base URL, e.g. http://host:8080   (required)
  --path PATH           endpoint path. Default: /<scenario>
  --data-dir DIR        generated payloads, overriding --capability

Describing the configuration under test. These are labels: they name what the
services were given, they do not set it. They appear in the run directory name
and in the report, so a matrix of runs is self-describing.
  --config NAME         short handle for the configuration under test; goes in
                        the run directory name
  --limit NAME=CPU/MEM  what one service was given, repeatable:
                          --limit adapter=1/1Gi --limit discovery=2/2Gi
                        A label, not a setting -- the real limits are dialled
                        in Helm. With SAMPLE=k8s the runner also asks the
                        cluster what the pods actually have.

Load:
  --threads N           concurrent threads          (default 10)
  --ramp-up SECONDS     time to start all threads   (default 30)
  --duration SECONDS    how long to run             (default 300)
  --rate-per-min N      target requests per minute; 0 means flat out (default 0)
  --loops N             iterations per thread. -1, the default, means keep
                        looping and let --duration decide when to stop. Setting
                        a positive number switches the run to iteration-count
                        mode and turns the scheduler off, so every thread
                        finishes its loops however long that takes
  --startup-delay SEC   wait before the first thread starts  (default 0)
  --on-sample-error W   continue | stoptest | stopthread    (default continue)
  --connect-timeout MS  TCP connect timeout                 (default 10000)
  --response-timeout MS how long to wait for a reply        (default 120000)

Resource sampling:
  --sample-source S     docker | k8s | none         (default none)
  --namespace NS        Kubernetes namespace
  --selector SEL        Kubernetes label selector, e.g. app=discovery
  --names A,B           container names, for docker
  --sample-interval S   seconds between samples     (default 5)
  --progress-interval S seconds between the running summaries JMeter prints
                        (default 300)

Reporting:
  --health-path PATH    measured for baseline round-trip time before the run
  --record-host         put the real hostname in the report. Off by default
  --note TEXT           a line describing this run, kept in the report
  --out DIR             where run directories are written (default ./results)
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --scenario)        SCENARIO="$2"; shift 2 ;;
    --capability)      CAPABILITY="$2"; shift 2 ;;
    --config)          CONFIG="$2"; shift 2 ;;
    --limit)           LIMITS="$LIMITS${LIMITS:+; }$2"; shift 2 ;;
    --url)             URL="$2"; shift 2 ;;
    --path)            ENDPOINT_PATH="$2"; shift 2 ;;
    --data-dir)        DATA_DIR="$2"; shift 2 ;;
    --threads)         THREADS="$2"; shift 2 ;;
    --ramp-up)         RAMP_UP="$2"; shift 2 ;;
    --duration)        DURATION="$2"; shift 2 ;;
    --rate-per-min)    RATE_PER_MIN="$2"; shift 2 ;;
    --loops)           LOOPS="$2"; shift 2 ;;
    --startup-delay)   STARTUP_DELAY="$2"; shift 2 ;;
    --on-sample-error) ON_SAMPLE_ERROR="$2"; shift 2 ;;
    --connect-timeout) CONNECT_TIMEOUT="$2"; shift 2 ;;
    --response-timeout) RESPONSE_TIMEOUT="$2"; shift 2 ;;
    --sample-source)   SAMPLE_SOURCE="$2"; shift 2 ;;
    --namespace)       NAMESPACE="$2"; shift 2 ;;
    --selector)        SELECTOR="$2"; shift 2 ;;
    --names)           NAMES="$2"; shift 2 ;;
    --sample-interval) SAMPLE_INTERVAL="$2"; shift 2 ;;
    --progress-interval) PROGRESS_INTERVAL="$2"; shift 2 ;;
    --health-path)     HEALTH_PATH="$2"; shift 2 ;;
    --record-host)     RECORD_HOST="yes"; shift ;;
    --note)            NOTE="$2"; shift 2 ;;
    --out)             OUT_ROOT="$2"; shift 2 ;;
    -h|--help)         usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

ENDPOINT_PATH="${ENDPOINT_PATH:-/$SCENARIO}"

# The test plans are capability-agnostic -- they post whatever payload files the
# payloads holds -- so the capability only decides which payloads to read.
CAPABILITY_SLUG="$(echo "$CAPABILITY" | tr '[:upper:]' '[:lower:]')"
DATA_DIR="${DATA_DIR:-$ROOT/data/$SCENARIO-payload/$CAPABILITY_SLUG}"
PLAN="$HERE/jmeter-jmx/$SCENARIO.jmx"

# ------------------------------------------------------------------ checks
#
# All of them up front. A benchmark that runs for ten minutes and then fails to
# write a report has wasted ten minutes, and a missing metrics-server is
# otherwise invisible until the report has an empty CPU column.

fail() { echo "bench: $*" >&2; exit 1; }

[ -n "$URL" ] || fail "--url is required"
[ -f "$PLAN" ] || fail "no test plan at $PLAN"
[ -f "$DATA_DIR/files.csv" ] || fail "no payloads at $DATA_DIR. Generate it first:
    make data CAPABILITY=$CAPABILITY"
command -v jmeter >/dev/null || fail "jmeter not found on PATH"

case "$SAMPLE_SOURCE" in
  docker)
    command -v docker >/dev/null || fail "--sample-source docker, but docker is not on PATH"
    [ -n "$NAMES" ] || fail "--sample-source docker needs --names"
    ;;
  k8s)
    command -v kubectl >/dev/null || fail "--sample-source k8s, but kubectl is not on PATH"
    kubectl top pod ${NAMESPACE:+--namespace "$NAMESPACE"} >/dev/null 2>&1 \
      || fail "kubectl top returned nothing. metrics-server must be installed in the
    cluster, and this machine needs a kubeconfig that can read it"
    ;;
  none|"") SAMPLE_SOURCE="none" ;;
  *) fail "--sample-source must be docker, k8s or none" ;;
esac

# Split the URL for JMeter, which wants the parts separately.
PROTOCOL="${URL%%://*}"
REST="${URL#*://}"
HOSTPORT="${REST%%/*}"
HOST="${HOSTPORT%%:*}"
PORT="${HOSTPORT##*:}"
if [ "$PORT" = "$HOST" ]; then
  [ "$PROTOCOL" = "https" ] && PORT=443 || PORT=80
fi

# A path in the URL is a prefix, not something to discard. Behind an ingress
# that mounts the service at /oan, the scenario's own /publish has to hang off
# it -- dropping it would post to the wrong place and say nothing about it.
URL_PREFIX=""
case "$REST" in
  */*) URL_PREFIX="/${REST#*/}"; URL_PREFIX="${URL_PREFIX%/}" ;;
esac
if [ -n "$URL_PREFIX" ]; then
  ENDPOINT_PATH="$URL_PREFIX$ENDPOINT_PATH"
  echo "bench: URL carries a path, so requests go to $ENDPOINT_PATH"
fi

# A Constant Throughput Timer divides by its rate, so a rate of 0 is not "no
# limit" -- it is an infinite wait, and the threads start and never send. 0
# stays the config's way of saying "flat out"; JMeter is handed a rate nothing
# will ever reach instead.
JMETER_RATE="$RATE_PER_MIN"
if [ "$JMETER_RATE" -le 0 ] 2>/dev/null; then
  JMETER_RATE=6000000
fi

# The scheduler and a loop count fight: with both on, JMeter stops at whichever
# comes first, so a run asking for 100 iterations quietly delivers however many
# fit in --duration. Deriving one from the other makes that impossible.
if [ "$LOOPS" -gt 0 ] 2>/dev/null; then
  SCHEDULER=false
else
  SCHEDULER=true
fi

RUN_ID="$(date -u +%Y%m%d-%H%M%S)"
# Named so a directory listing IS the matrix: when it ran, what the services
# were given, and what was driven at them.
RUN_DIR="$OUT_ROOT/$RUN_ID-$CONFIG-$CAPABILITY_SLUG-$SCENARIO"
mkdir -p "$RUN_DIR"

echo "bench: $CAPABILITY $SCENARIO -> $RUN_DIR"

# --------------------------------------------------------------- baseline
#
# How much of the latency is the network between here and the cluster. Without
# it, a reader cannot tell a slow service from a distant one.

BASELINE="not measured"
if [ -n "$HEALTH_PATH" ] && command -v curl >/dev/null; then
  total=0
  count=0
  misses=0
  for _ in $(seq 1 20); do
    # Give up after two failures rather than spending 20 timeouts, at five
    # seconds each, to learn the same thing.
    if [ "$misses" -ge 2 ]; then
      break
    fi
    # -f so an HTTP error counts as a failure too, and the exit status rather
    # than the output: curl prints a timing even when the connection is
    # refused, so an empty-string check would count every failure as a sample.
    if ! t=$(curl -sf -o /dev/null -w '%{time_total}' --max-time 5 "$URL$HEALTH_PATH" 2>/dev/null); then
      misses=$((misses + 1))
      continue
    fi
    total=$(awk -v a="$total" -v b="$t" 'BEGIN { print a + b }')
    count=$((count + 1))
  done
  if [ "$count" -gt 0 ]; then
    BASELINE="$(awk -v t="$total" -v c="$count" 'BEGIN { printf "%.1f ms (mean of %d)", (t / c) * 1000, c }')"
  else
    BASELINE="health check unreachable"
  fi
fi

# ------------------------------------------------------- what the pods got
#
# CPU= and MEMORY= are what you SAY the services were given. This asks the
# cluster what they were ACTUALLY given, so a mislabelled run is visible rather
# than quietly wrong. Best effort: no kubeconfig, no answer, and the run
# continues on the labels alone.

OBSERVED_LIMITS=""
if [ "$SAMPLE_SOURCE" = "k8s" ]; then
  OBSERVED_LIMITS="$(kubectl get pods \
    ${NAMESPACE:+--namespace "$NAMESPACE"} \
    ${SELECTOR:+--selector "$SELECTOR"} \
    -o jsonpath='{range .items[*]}{.metadata.labels.app\.kubernetes\.io/name}{" "}{.spec.containers[0].resources.limits.cpu}{"/"}{.spec.containers[0].resources.limits.memory}{"; "}{end}' \
    2>/dev/null | sed 's/; $//')"
  if [ -n "$OBSERVED_LIMITS" ]; then
    echo "bench: pods report limits of $OBSERVED_LIMITS"
  fi
fi

# ---------------------------------------------------------------- sampling

SAMPLER_PID=""
if [ "$SAMPLE_SOURCE" != "none" ]; then
  "$HERE/sample_resources.sh" \
    --source "$SAMPLE_SOURCE" \
    --interval "$SAMPLE_INTERVAL" \
    --out "$RUN_DIR/resources.csv" \
    ${NAMES:+--names "$NAMES"} \
    ${NAMESPACE:+--namespace "$NAMESPACE"} \
    ${SELECTOR:+--selector "$SELECTOR"} &
  SAMPLER_PID=$!
  echo "bench: sampling $SAMPLE_SOURCE every ${SAMPLE_INTERVAL}s (pid $SAMPLER_PID)"
fi

stop_sampler() {
  if [ -n "$SAMPLER_PID" ] && kill -0 "$SAMPLER_PID" 2>/dev/null; then
    kill "$SAMPLER_PID" 2>/dev/null || true
    wait "$SAMPLER_PID" 2>/dev/null || true
  fi
}
trap stop_sampler EXIT INT TERM

# ------------------------------------------------------------------- load

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# JMeter prints a banner and ASCII art before its version, so the version is
# the last number-looking thing in the output, not the first line.
#
# Written BEFORE the load, so a run killed halfway still says how it was
# configured. Without it a partial results.jtl cannot be interpreted at all.
# `ended` is appended when the load finishes.
REPORT_HOST="$HOST"
[ "$RECORD_HOST" = "yes" ] || REPORT_HOST="(redacted)"

cat > "$RUN_DIR/run.env" <<EOF
scenario=$SCENARIO
capability=$CAPABILITY
config=$CONFIG
limits=$LIMITS
observedLimits=$OBSERVED_LIMITS
tool=JMeter $(jmeter --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | tail -1 || true)
started=$STARTED_AT
host=$REPORT_HOST
port=$PORT
path=$ENDPOINT_PATH
threads=$THREADS
rampUp=$RAMP_UP
duration=$DURATION
ratePerMin=$RATE_PER_MIN
loops=$LOOPS
scheduler=$SCHEDULER
connectTimeout=$CONNECT_TIMEOUT
responseTimeout=$RESPONSE_TIMEOUT
baseline=$BASELINE
dataDir=$DATA_DIR
sampleSource=$SAMPLE_SOURCE
sampleInterval=$SAMPLE_INTERVAL
progressInterval=$PROGRESS_INTERVAL
note=$NOTE
EOF

# One writer for results.jtl, and only one: `-l` here. The plans carry no
# listener of their own -- two writers on the same path interleave their rows.
set +e
jmeter -n -t "$PLAN" \
  -l "$RUN_DIR/results.jtl" \
  -j "$RUN_DIR/jmeter.log" \
  -e -o "$RUN_DIR/html" \
  -Jprotocol="$PROTOCOL" -Jhost="$HOST" -Jport="$PORT" -Jpath="$ENDPOINT_PATH" \
  -JdataDir="$DATA_DIR" \
  -Jthreads="$THREADS" -JrampUp="$RAMP_UP" -Jduration="$DURATION" \
  -JthroughputPerMin="$JMETER_RATE" \
  -Jloops="$LOOPS" -Jscheduler="$SCHEDULER" \
  -JstartupDelay="$STARTUP_DELAY" -JonSampleError="$ON_SAMPLE_ERROR" \
  -JconnectTimeout="$CONNECT_TIMEOUT" -JresponseTimeout="$RESPONSE_TIMEOUT" \
  -Jsummariser.interval="$PROGRESS_INTERVAL" \
  -Jjmeter.save.saveservice.latency=true \
  -Jjmeter.save.saveservice.connect_time=true \
  -Jjmeter.save.saveservice.thread_counts=true \
  -Jjmeter.save.saveservice.bytes=true \
  -Jjmeter.save.saveservice.sent_bytes=true \
  -Jjmeter.save.saveservice.assertion_results_failure_message=true \
  -Jjmeter.save.saveservice.print_field_names=true \
  | tee "$RUN_DIR/progress.log"
JMETER_STATUS=${PIPESTATUS[0]}
set -e

ENDED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
stop_sampler

[ "$JMETER_STATUS" -eq 0 ] || echo "bench: jmeter exited $JMETER_STATUS -- reporting on what it wrote" >&2

# ----------------------------------------------------------------- report

echo "ended=$ENDED_AT" >> "$RUN_DIR/run.env"

"$HERE/report.sh" --run-dir "$RUN_DIR" --data-dir "$DATA_DIR"

echo
echo "bench: report written to $RUN_DIR/report.md"
echo "bench: JMeter's own dashboard is at $RUN_DIR/html/index.html"
echo
echo "bench: the run window, for searching a dashboard afterwards"
echo "         from  $STARTED_AT"
echo "         to    $ENDED_AT"
