# Benchmark

Load tests for the OAN network layer: how much it handles, how fast it answers,
and what it costs in CPU and memory. Three scenarios — publish, discover and
select — driven by JMeter against a deployed stack.

Sections 1 to 5 are in the order you go through them: check what is here,
get the prerequisites in place, build the data, run, read the results.
Reference at the end is background, not a step.

`make` on its own prints the menu.

## 1. Layout

```
benchmark/
├── Makefile
├── capabilities/            one folder per capability
│   └── mandiprice/
│       ├── config/             publish.yaml, discover.yaml, select.yaml
│       ├── templates/          catalog.json, select.json
│       └── tools/
│           ├── fetch-metadata/
│           ├── prepare-publish-data/
│           ├── prepare-discover-data/
│           └── prepare-select-data/
├── tools/                  shared by every capability
│   ├── run-benchmark.sh
│   ├── report.sh
│   ├── sample_resources.sh
│   └── jmeter-jmx/             publish.jmx, discover.jmx, select.jmx
├── data/
│   ├── mandiPrice-metadata/    committed: the input, fetched from Agmarknet
│   ├── CHECKSUMS               committed: a digest per generated file
│   └── *-payload/              gitignored: built by `make data`
└── results/                gitignored
```

`mock-upstream/` holds a stand-in for select's upstream API. It is applied by
hand and is not part of any chart — see [Prerequisites](#prerequisites).

## 2. Prerequisites

### 2.1 On the machine you run from

| | Why |
|---|---|
| Go 1.22+ | builds the data tools |
| JMeter 5.6+ | drives the load, must be on your PATH |
| `curl`, `python3` | baseline timing, reading the payload manifest |
| `kubectl` | only if you want the harness to sample CPU and memory itself |

No JMeter plugins needed. Check what is missing before anything else:

```bash
make dependency-check
```

### 2.2 The deployed stack

Everything in the path of the scenario you are running has to be up and
reachable: the adapters, discovery and its database, and the registry seeded
with the adapter identities. Every adapter resolves its caller against the
registry on each request, so the registry is in the path of all three scenarios
even though no scenario addresses it directly.

**No hostname is committed anywhere.** The target is a `URL=` argument, the pods
to sample are `NAMESPACE=` and `SELECTOR=`, and the report hides the host unless
you pass `--record-host`. So the ingress can be anything you like, as long as it
routes the three paths to the right place:

| Path | Goes to |
|---|---|
| `/publish` | the provider adapter |
| `/discover` | the consumer adapter |
| `/select` | the consumer adapter |

Point `URL=` at that host with no path — the runner appends the action itself.

**Good to have, not required:** something collecting per-pod CPU and memory.
Either metrics-server, which lets the harness sample `kubectl top` itself with
`SAMPLE=k8s`, or an OpenTelemetry agent feeding a dashboard. With neither, a run
still records throughput, latency and errors, and nothing else.

### 2.3 For select only

Select is the one scenario that leaves the network: the provider adapter calls a
real API and maps what comes back. Running it against the real one measures that
API rather than this stack, and puts load on somebody else's service. A stand-in
is included:

```bash
kubectl apply -f mock-upstream/manifest.yaml
```

nginx returning one fixed response from a ConfigMap — nothing to build, nothing
to publish to a registry. `kubectl delete -f` the same file when the run is over;
it is deliberately outside Argo CD so nothing reconciles it back.

Three things in the deployment have to agree with it, and none of them live in
this directory:

- the registry's upstream for the capability must carry the mock's in-cluster
  address, `http://mock-upstream.mock-upstream.svc.cluster.local`
- the provider adapter's auth for that binding must be `none`; a mock has no
  credential to present, and the real scheme fetches a token from a URL held in
  a Secret
- the binding's mapping URL must name a tag that still exists — the provider
  fetches it per binding, and a deleted tag returns 404, which surfaces as a
  NACK with an internal error that says nothing about mappings

**Changing a value is not enough.** The registry only creates rows it does not
already have, so the seed job skips anything present and reports "already
present". Delete the row first, then re-run the seed.

Send one select by hand before a full run. It should return 200 with resources in
the body; anything else means one of the three above is still wrong.

## 3. Prepare the data

Build the payloads once, then run as often as you like.

```bash
make data
```

About 45 seconds. It produces 100,000 resources and 20,000 discover queries,
roughly 150 MB — too much for a public repository, so it is generated rather
than committed.

Generation is **seeded**: rebuilding gives byte-identical files, which is what
makes two runs comparable. Building needs no credentials and no network.

**Rebuild the payloads.** `data` does all three; the others do one each.

```bash
make data           CAPABILITY=MandiPrice
make publish-data   CAPABILITY=MandiPrice
make discover-data  CAPABILITY=MandiPrice
make select-data    CAPABILITY=MandiPrice
```

**Check the payloads match their config.** Rebuilds, then fails if anything
differs from the digests in `data/CHECKSUMS`.

```bash
make verify-data CAPABILITY=MandiPrice
```

**Re-record the digests**, after changing a config, a template or the metadata
on purpose. Commit `data/CHECKSUMS` along with that change.

```bash
make checksums CAPABILITY=MandiPrice
```

### 3.1 How the data fits together

```
data/<capability>-metadata/   fetched from the provider
        ▼
data/publish-payload/         catalogs and resources
        ▼
data/discover-payload/        queries
data/select-payload/          select requests
```

Discover and select are built from what publish produced, so a query cannot ask
for something unpublished and a select cannot name a resource that does not
exist.

Generation is seeded: rebuilding gives byte-identical files. That is what makes
two runs comparable, and it is why the payloads need not be committed —
`make verify-data` rebuilds and compares against `data/CHECKSUMS`, failing if
anything differs. Building needs no credentials and no network; only refetching
the metadata does.

If you change a config, a template or the metadata on purpose, re-record the
digests with `make checksums` and commit that file with the change.

To change how much data, edit `capabilities/<name>/config/`. To change the shape
of a payload, edit `capabilities/<name>/templates/`.

### 3.2 Refetching the provider metadata

Only needed to refresh the input the payloads are built from. This is the one
step that needs credentials and network access.

**Refetch provider data.** Needs credentials in the environment.

```bash
export AGMARKNET_TOKEN_URL=...      # exchanges credentials for a token
export AGMARKNET_MASTER_URL=...     # states, districts, markets, commodities
export AGMARKNET_MAPPING_URL=...    # which commodities each market trades
export AGMARKNET_ACCESS_NAME=...
export AGMARKNET_PASSWORD=...
export AGMARKNET_TOKEN=...          # optional, skips the exchange

# STATES empty fetches every state, which is what the 100,000-resource
# target needs. Narrow it only for a smaller run.
make get-mandi-metadata \
  STATES='' \
  FROM_DATE=01-01-2026 \
  TO_DATE=01-12-2026
```

## 4. Run a benchmark

`publish`, `discover` or `select` — the same settings for all three.

**Run a benchmark.** `publish`, `discover` or `select` — same settings for all
three.

```bash
make publish \
  URL=http://<host>:<port> \
  CAPABILITY=MandiPrice \
  THREADS=20 RAMP_UP=30 DURATION=600 \
  LOOPS=-1 RATE=0 \
  STARTUP_DELAY=0 ON_SAMPLE_ERROR=continue \
  CONNECT_TIMEOUT=10000 RESPONSE_TIMEOUT=120000 \
  CONFIG=all-1cpu \
  CONSUMER_CPU=1 CONSUMER_MEM=1Gi \
  NETWORK_CPU=1 NETWORK_MEM=1Gi \
  PROVIDER_CPU=1 PROVIDER_MEM=1Gi \
  DISCOVERY_CPU=1 DISCOVERY_MEM=1Gi \
  DISCOVERY_DB_CPU=1 DISCOVERY_DB_MEM=1Gi \
  REGISTRY_CPU=1 REGISTRY_MEM=1Gi \
  REGISTRY_DB_CPU=1 REGISTRY_DB_MEM=1Gi \
  SAMPLE=k8s NAMESPACE=<namespace> SELECTOR=app=discovery \
  SAMPLE_INTERVAL=5 PROGRESS_INTERVAL=300 \
  HEALTH=/healthz \
  NOTE='1 cpu baseline, mock provider' \
  OUT=./results \
  ARGS='--record-host'
```

### 4.1 Settings

| Variable | Used by | Default | Does |
|---|---|---|---|
| `URL` | the three run commands | required | where to send requests |
| `CAPABILITY` | run commands, `data` | `MandiPrice` | which `capabilities/<slug>/` to use |
| `THREADS` | run commands | `10` | concurrent threads |
| `RAMP_UP` | run commands | `30` | seconds to start all threads |
| `DURATION` | run commands | `300` | seconds to run |
| `LOOPS` | run commands | `-1` | requests per thread; a positive value replaces `DURATION` |
| `RATE` | run commands | `0` | target requests per minute, 0 is flat out |
| `CONFIG` | run commands | `na` | short handle for the run directory name |
| `CONSUMER_CPU` `CONSUMER_MEM` | run commands | — | the consumer adapter |
| `NETWORK_CPU` `NETWORK_MEM` | run commands | — | the network adapter |
| `PROVIDER_CPU` `PROVIDER_MEM` | run commands | — | the provider adapter |
| `DISCOVERY_CPU` `DISCOVERY_MEM` | run commands | — | discovery-service |
| `DISCOVERY_DB_CPU` `DISCOVERY_DB_MEM` | run commands | — | discovery's Postgres |
| `REGISTRY_CPU` `REGISTRY_MEM` | run commands | — | the registry |
| `REGISTRY_DB_CPU` `REGISTRY_DB_MEM` | run commands | — | the registry's Postgres |
| `SAMPLE` | run commands | `none` | `k8s`, `docker` or `none` |
| `NAMESPACE` | run commands | — | namespace to sample, for `SAMPLE=k8s` |
| `SELECTOR` | run commands | — | narrows the sampling; **leave unset to sample every pod in the namespace**, which is usually what you want |
| `NAMES` | run commands | — | which containers to sample, for `SAMPLE=docker` |
| `HEALTH` | run commands | — | path timed before the run, as a baseline |
| `NOTE` | run commands | — | a line kept in the report |
| `PROGRESS_INTERVAL` | run commands | `300` | seconds between progress lines |
| `SAMPLE_INTERVAL` | run commands | `5` | seconds between CPU/memory samples |
| `OUT` | run commands | `./results` | where run directories go |
| `ARGS` | run commands | — | extra flags passed to the runner |
| `STARTUP_DELAY` | run commands | `0` | seconds before the first thread |
| `ON_SAMPLE_ERROR` | run commands | `continue` | `continue`, `stoptest`, `stopthread` |
| `CONNECT_TIMEOUT` | run commands | `10000` | connect timeout, ms |
| `RESPONSE_TIMEOUT` | run commands | `120000` | reply timeout, ms |
| `STATES` | `get-mandi-metadata` | empty, all states | which states to fetch |
| `FROM_DATE` `TO_DATE` | `get-mandi-metadata` | 2026 | window for the commodity data |

The limit variables set nothing — you dial the real limits in Helm. They record
what you dialled, one row per service in the report. Leave unset whatever a
scenario does not touch.

The deployment has three adapters with different jobs, so which limits matter
depends on the scenario:

| Scenario | Path |
|---|---|
| publish | consumer → network → discovery → discovery's database |
| discover | consumer → network → discovery → discovery's database |
| select | consumer → provider → the upstream provider |

Discover goes through the network adapter; select does not. The consumer adapter
hands a select straight to the provider.

Every adapter reads the registry to verify its caller, so the registry and its
database sit in the path of all three.

**Set at least the ones the scenario touches.** These are labels, not settings —
nothing reads them back. Leave them all unset and the run records no limits at
all, so the results cannot say what hardware produced them.

`CONFIG` is a short handle for the whole configuration, because seven services'
limits will not fit in a directory name:

```
results/20260922-101500-all-1cpu-mandiprice-discover/
```

The sampler writes its own file, `resources.csv`, one row per pod per sample,
and the report averages and peaks it per pod:

```
| Component            | CPU mean | CPU peak | Mem mean | Mem peak |
| network-adapter      |      679 |      972 |      248 |      309 |
| discovery-service    |      860 |     1087 |      366 |      478 |
| discovery-postgres   |     1224 |     1889 |      656 |      899 |
```

With `SAMPLE=k8s` the runner also asks the cluster what the pods *actually* have,
and the report shows both. If they disagree, the label is wrong:

```
| Service            | As labelled | As deployed |
| network-adapter    | 1/1Gi       | 500m/512Mi  |
| discovery-service  | 1/1Gi       | 1/512Mi     |
| discovery-postgres | 1/1Gi       | 2/2Gi       |
```

## 5. After a run

One directory per run, under `results/`, named for when it ran and what it ran
against:

```
results/<timestamp>-<config>-<capability>-<scenario>/

results/20260922-101500-all-1cpu-mandiprice-publish/
results/20260922-104500-network2-mandiprice-discover/
```

`<config>` comes from `CONFIG=`, so a directory listing is the matrix of what
was tested. Inside:

| File | Holds |
|---|---|
| `report.md` | the summary, read this one |
| `results.jtl` | every request, as JMeter recorded it |
| `resources.csv` | CPU and memory samples — only when `SAMPLE` is set; the default is `none` |
| `progress.log` | the progress lines, every `PROGRESS_INTERVAL` seconds |
| `html/index.html` | JMeter's dashboard, with charts |
| `run.env` | the settings this run used |

Runs are never deleted for you. `make clean` only clears the build cache;
`make clean-results` asks before deleting.

### 5.1 Reading the numbers

**Response time includes the network.** The load generator sits outside the
cluster. Pass `HEALTH=` and the report records a baseline round trip, so you can
tell a slow service from a distant one.

**Throughput is what was achieved, not what was asked for.** Threads wait for a
reply before sending again, so a slow service receives less traffic.

**Peak CPU is the highest value seen**, at your sampling interval. A shorter
spike is missed.

**A 200 does not mean publish succeeded.** Discovery returns 200 with per-catalog
verdicts in the body. The error rate counts HTTP failures only.

**CPU and memory can come from either side, and the default is neither.**
`SAMPLE` defaults to `none`, so a plain run records no resource figures at all.

With `SAMPLE=k8s` the harness samples `kubectl top` itself and writes
`resources.csv`, which needs metrics-server in the cluster.

If the cluster runs an OpenTelemetry agent collecting per-pod metrics, the
dashboard has them instead, and they are readable after the run rather than only
during it — which is what the run window in `report.md` is for. Disk and network
I/O are collected by neither.

### 5.2 Bringing runs back

**Bring runs back from the load machine.** The load generator usually runs
somewhere else. This copies whole run directories across — `results.jtl`
included, because that is the evidence; `report.md` is only arithmetic done on
it.

```bash
make fetch-results REMOTE=user@host:/path/to/benchmark/results
```

Runs are named by timestamp, so nothing collides and re-running only brings
across what is missing.

**Read the results.** `results` is one line per run; `report` prints a whole
one, newest by default.

```bash
make results
make report
make report RUN=20260921-101500-1cpu-1Gi-mandiprice-publish
```

```
20260921-101500-1cpu-1Gi-mandiprice-publish    1949 reqs  149.92 requests/second  p95 30 ms   err 0.00 %
20260921-104500-2cpu-2Gi-mandiprice-discover    500 reqs   50.00 requests/second  p95 112 ms  err 2.00 %
```

### 5.3 Tidying up

**Tidy up.** Both take nothing. `clean` does not touch runs or payloads;
`clean-results` lists what it will delete and asks first.

```bash
make clean
make clean-results
```

---

# Reference

## MandiPrice

Commodity prices from agricultural markets, served by Agmarknet.

One catalog per state, one resource per **market-commodity pair**.

That pairing is the important bit. A market trades anywhere from 1 to 80
commodities, median 4. Publishing a market as one resource carrying all of them
made every resource a different size — and so made one discover response 5 MB
and the next 1 MB, from queries matching the same number of things. One
commodity per resource makes every resource the same shape, within about 2%.

```
derived 9 catalog(s) from state_name
1002 payload(s), 9 catalog(s), 100000 resources from 19135 real
market-commodity pairs (x5.23), 135.8 MB
skipped 2221 market(s) with no commodities recorded in the mapping
skipped 583 market(s) with no coordinates
skipped 9 market(s) whose coordinates are not plausible
```

**Nine catalogs, not thirty-six.** Agmarknet serves master data for 36 states
but a commodity mapping for only 9 — Maharashtra, Tamil Nadu, Madhya Pradesh,
Uttar Pradesh, Karnataka, Chhattisgarh, Meghalaya, Andhra Pradesh and Bihar. A
market with no commodities recorded cannot be a MandiPrice resource, so those
states contribute nothing and no empty catalog is created for them.

`targetResources` in `config/publish.yaml` sets the total. The real pairs are
repeated to reach it, each state taking a share proportional to what it really
has, and the total lands on the number exactly. `realPairs` in the manifest says
how far the real data was stretched — worth reading before quoting a result.

Those skips are the source data, not a bug. Some markets have no coordinates;
a few have coordinates that cannot be true, such as a longitude of 703620 or a
market called "Testing". They are dropped and named rather than corrected.

**Discover queries** carry a commodity filter and a spatial constraint together,
never one alone. Every query is distinct, and every one returns the same number
of resources.

Distinct, because a query's centre is placed anywhere on the line between two
markets either side of a state border — continuously, not at the midpoint. A
midpoint would give one query per border pair, and there are only 411 pairs.

The same size, because the extent is not guessed. The generator knows every
published resource from `resources.csv`, sorts that commodity's resources by
distance from the centre, and puts the circle's edge between the last one it
wants and the first it does not. Boxes are bisected to the same end.

```
20000 queries from 636 border pairs
    S_DWITHIN + filter       9768
    S_INTERSECTS + filter    10232
    matches per query        min 67, median 76, max 83
    states per query         1:0  2:16070  3+:3930
    distinct queries         20000 of 20000
```

The spread that remains is the data's, not the method's: copies of one market
share its coordinates, so the reachable counts step by about ten rather than one
at a time. `matches.tolerance` is what absorbs that.

**How many queries do you need?** Requests sent is roughly
`threads / latency x duration`, so 20 threads for 20 minutes at 100 ms each is
about 240,000 requests. JMeter recycles the list, so 20,000 queries means each
question is re-asked about a dozen times, spread far apart. Raise `queries` in
the config to widen that; it costs about a second per thousand.

**Select requests** each name one published resource, which is one market and
one commodity that market really trades.

### Refreshing the data

```bash
make get-mandi-metadata
```

Writes `data/mandiPrice-metadata/mandi-metadata.json`: states, districts,
markets, commodities, and which commodities each market trades. Needs the
credentials above.

`STATES` is empty by default, which fetches **all** of them — that is what the
100,000-resource target needs: all-India yields about 19k real pairs against
10k for five states, which halves how far the data is stretched.
Narrow it with `STATES='Karnataka,Kerala'`. The catalogs follow whatever was
fetched: `config/publish.yaml` lists no entries, so one catalog is derived per
state present in the metadata.

The last part comes from a mapping endpoint, one call per state, over a date
window:

```bash
make get-mandi-metadata FROM_DATE=01-01-2026 TO_DATE=01-12-2026
```

A market with no trades in that window gets no commodities and is skipped.

### Known quirks

**Coverage is uneven.** Gujarat has no mapping data at all, which is why Madhya
Pradesh is used instead. Andhra Pradesh has four markets.

**Two code spaces.** A commodity's `code` in a payload is `commodity_id`, not
`agm_commodity_code` — they differ for 531 of 560 commodities. Tomato is 65 and
78. The same applies to districts and states: the `agm_*_code` columns are not
the ones a payload uses.

---

## Adding a capability

Each capability brings its own data preparation, because payload shapes differ
too much to share one tool.

```
capabilities/<name>/
├── config/       publish.yaml, discover.yaml, select.yaml
├── templates/    the payload shapes
└── tools/        a metadata fetcher and three prepare-* tools
```

Copy `capabilities/mandiprice/` and adapt: the fetcher to that provider's API,
the tools to that payload's fields.

Then:

```bash
make data     CAPABILITY=<Name>
make discover CAPABILITY=<Name> URL=http://<host>:<port>
```

The runner, the JMeter plans, the sampler and the report do not change. A plan
posts whatever files the payload directory holds; it never knows which capability
it is running.
