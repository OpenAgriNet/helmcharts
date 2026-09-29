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
├── mock-upstream/          a stand-in for select's upstream, applied by hand
├── data/
│   ├── mandiPrice-metadata/    committed: the input, fetched from Agmarknet
│   ├── CHECKSUMS               committed: a digest per generated file
│   └── *-payload/              gitignored: built by `make data`
└── results/                gitignored
```

`mock-upstream/` is not part of any chart — see [2.3](#23-for-select-only).

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

Up and reachable before any run:

- the adapters
- discovery and its database
- the registry, seeded with the adapter identities — every adapter resolves its
  caller against it on every request, so it is in the path of all three
  scenarios even though none addresses it directly

**No hostname is committed anywhere.**

- the target is a `URL=` argument
- the pods to sample are `NAMESPACE=` and `SELECTOR=`
- the report hides the host unless you pass `--record-host`

So the ingress can be anything, as long as it routes:

| Path | Goes to |
|---|---|
| `/publish` | the provider adapter |
| `/discover` | the consumer adapter |
| `/select` | the consumer adapter |

Point `URL=` at that host with no path — the runner appends the action.

**Good to have, not required** — something collecting per-pod CPU and memory:

- metrics-server, so the harness can sample `kubectl top` with `SAMPLE=k8s`
- or an OpenTelemetry agent feeding a dashboard

With neither, a run records throughput, latency and errors, and nothing else.

### 2.3 For select only

Select is the one scenario that leaves the network — the provider adapter calls
a real API. Running it against the real one measures that API, not this stack,
and puts load on someone else's service. A stand-in is included:

```bash
kubectl apply -f mock-upstream/manifest.yaml
```

nginx serving one fixed response from a ConfigMap. `kubectl delete -f` the same
file afterwards; it sits outside Argo CD so nothing reconciles it back.

Three things in the deployment have to match it, none of them in this directory:

| | Must be |
|---|---|
| the registry's upstream for the capability | `http://mock-upstream.mock-upstream.svc.cluster.local` |
| the provider's auth for that binding | `none` — a mock has no credential |
| the binding's mapping URL | a tag that still exists; a deleted one 404s and surfaces as a NACK saying nothing about mappings |

**Changing a value is not enough.** The seed only creates rows it does not
already have, and reports "already present" for the rest. Delete the row, then
re-run the seed.

Send one select by hand first. It should return 200 with resources in the body.

## 3. Prepare the data

```
data/<capability>-metadata/   fetched from the provider     <- 3.1, rarely
        ▼
data/publish-payload/         catalogs and resources        <- 3.2, once
        ▼
data/discover-payload/        queries
data/select-payload/          select requests
```

Discover and select are built from what publish produced, so a query cannot ask
for something unpublished and a select cannot name a resource that does not
exist.

### 3.1 Refetch the provider metadata

**Skip this unless the input needs refreshing** — the metadata is committed, and
this is the only step needing credentials and network access.

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

### 3.2 Build the payloads

```bash
make data
```

- about 45 seconds
- 100,000 resources and 20,000 discover queries, around 150 MB
- the 100,000 publish resources arrive as **1,002 payload files**, one request each, so a single pass over the set is 1,002 requests
- a run asking for more than that starts the list again from the top, publishing the set more than once
- generated rather than committed: too much for a public repository
- seeded, so rebuilding gives byte-identical files and two runs stay comparable
- needs no credentials and no network

| Command | Does |
|---|---|
| `make data` | all three payload sets |
| `make publish-data` | one set each |
| `make discover-data` | |
| `make select-data` | |
| `make verify-data` | rebuilds and fails if anything differs from `data/CHECKSUMS` |
| `make checksums` | re-records the digests, after changing a config or template on purpose. Commit `data/CHECKSUMS` with that change |

All take `CAPABILITY=`. To change how much data, edit `capabilities/<name>/config/`;
to change the shape of a payload, edit `capabilities/<name>/templates/`.

## 4. Run a benchmark

The minimum:

```bash
make publish URL=https://<host>
```

`publish`, `discover` and `select` take the same settings. A realistic run names
the load, labels the hardware it ran against, and samples it:

```bash
make discover URL=https://<host> \
  THREADS=20 RAMP_UP=10 LOOPS=500 \
  CONFIG=disc-db4 \
  DISCOVERY_CPU=1 DISCOVERY_MEM=1Gi \
  DISCOVERY_DB_CPU=4 DISCOVERY_DB_MEM=4Gi \
  SAMPLE=k8s NAMESPACE=discovery \
  NOTE='discovery 1 cpu, database 4'
```

Everything else is below.

### 4.1 Settings

Only `URL` is required. Everything else has a default.

**Where to send it**

| Variable | Default | What it does |
|---|---|---|
| `URL` | required | Host to drive, no path — the runner appends the action |
| `CAPABILITY` | `MandiPrice` | Which `capabilities/<slug>/` to use |
| `HEALTH` | — | Path timed before the run, as a baseline round trip |

**How hard to push**

| Variable | Default | What it does |
|---|---|---|
| `THREADS` | `10` | Requests in flight at once. Each waits for its reply before sending again |
| `RAMP_UP` | `30` | Seconds to start all the threads |
| `DURATION` | `300` | Seconds to keep going. Ignored when `LOOPS` is positive |
| `LOOPS` | `-1` | Requests **per thread** — a run sends `LOOPS` × `THREADS` in all, so 10 threads and 501 loops is 5,010 requests. `-1` loops until `DURATION` ends it; a positive number runs exactly that many, however long it takes |
| `RATE` | `0` | Ceiling on requests per minute. `0` is flat out |
| `FRESH_IDS` | `no` | Publish only. `yes` rewrites the catalog and resource ids in every payload, so no two requests write to the same catalog. `no` leaves them as generated, so requests contend for the catalogs already stored — the realistic case, and much slower. The report records which was used |
| `STARTUP_DELAY` | `0` | Seconds to wait before the first thread starts |
| `CONNECT_TIMEOUT` | `10000` | Milliseconds to wait for the connection to open before giving up on a request |
| `RESPONSE_TIMEOUT` | `120000` | Milliseconds to wait for the reply. Keep it above every timeout in the stack, or you measure the load generator giving up |
| `ON_SAMPLE_ERROR` | `continue` | What a failed request does: `continue` counts it, `stopthread` retires that thread, `stoptest` ends the run |

**What to call it, and what it ran against**

| Variable | Default | What it does |
|---|---|---|
| `CONFIG` | `na` | Short handle in the run directory name — `disc-db4`, `all-1cpu`. Makes a directory listing read as the matrix |
| `NOTE` | — | One line kept in the report, for anything the handle cannot carry |
| `CONSUMER_CPU` `CONSUMER_MEM` | — | Records what the consumer adapter was given. Sets nothing |
| `NETWORK_CPU` `NETWORK_MEM` | — | Records what the network adapter was given |
| `PROVIDER_CPU` `PROVIDER_MEM` | — | Records what the provider adapter was given |
| `DISCOVERY_CPU` `DISCOVERY_MEM` | — | Records what discovery-service was given |
| `DISCOVERY_DB_CPU` `DISCOVERY_DB_MEM` | — | Records what discovery's Postgres was given |
| `REGISTRY_CPU` `REGISTRY_MEM` | — | Records what the registry was given |
| `REGISTRY_DB_CPU` `REGISTRY_DB_MEM` | — | Records what the registry's Postgres was given |

**What to sample while it runs**

| Variable | Default | What it does |
|---|---|---|
| `SAMPLE` | `none` | Where CPU and memory come from. `k8s` polls `kubectl top`, needs metrics-server; `docker` polls `docker stats`; `none` collects neither |
| `NAMESPACE` | — | Which namespace to sample, for `SAMPLE=k8s` |
| `SELECTOR` | — | Narrows the sampling to matching pods; **leave unset to sample every pod in the namespace**, which is usually what you want |
| `NAMES` | — | Which containers to sample, for `SAMPLE=docker` |
| `SAMPLE_INTERVAL` | `5` | Seconds between samples. A shorter spike is missed |
| `PROGRESS_INTERVAL` | `300` | Seconds between progress lines while the run is going |

**Where the output goes**

| Variable | Default | What it does |
|---|---|---|
| `OUT` | `./results` | Where run directories are written |
| `ARGS` | — | Passed to the runner untouched. `--record-host` writes the real hostname into the report instead of hiding it |

**Fetching provider metadata** — used by `get-mandi-metadata` only

| Variable | Default | What it does |
|---|---|---|
| `STATES` | empty | Which states to fetch. Empty fetches all, which the 100,000-resource target needs |
| `FROM_DATE` `TO_DATE` | 2026 | Window the commodity mapping is fetched over. A market with no trades in it is skipped |

**The limit variables set nothing.** You dial the real limits in Helm; these
record what you dialled, one row per service in the report. Which ones matter
depends on the scenario:

| Scenario | Path |
|---|---|
| publish | consumer → network → discovery → discovery's database |
| discover | consumer → network → discovery → discovery's database |
| select | consumer → provider → the upstream provider |

Discover goes through the network adapter; select does not. Every adapter reads
the registry to verify its caller, so the registry and its database sit in the
path of all three.

Leave them all unset and the run records no limits at all, so the results cannot
say what hardware produced them.

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

With `SAMPLE` set, `resources.csv` holds one row per pod per sample, and the
report averages and peaks it:

```
| Component            | CPU mean | CPU peak | Mem mean | Mem peak |
| network-adapter      |      679 |      972 |      248 |      309 |
| discovery-service    |      860 |     1087 |      366 |      478 |
| discovery-postgres   |     1224 |     1889 |      656 |      899 |
```

With `SAMPLE=k8s` it also asks the cluster what the pods *actually* have and
shows both. If they disagree, the label is wrong:

```
| Service            | As labelled | As deployed |
| network-adapter    | 1/1Gi       | 500m/512Mi  |
| discovery-service  | 1/1Gi       | 1/512Mi     |
| discovery-postgres | 1/1Gi       | 2/2Gi       |
```

### 5.1 Reading the numbers

**Response time includes the network.** The load generator sits outside the
cluster. Pass `HEALTH=` to record a baseline round trip.

**Throughput is what was achieved, not what was asked for.** Threads wait for a
reply before sending again, so a slow service receives less traffic.

**Peak CPU is the highest value seen**, at your sampling interval. A shorter
spike is missed.

**A 200 does not mean publish succeeded.** Discovery returns 200 with
per-catalog verdicts in the body. The error rate counts HTTP failures only.

**CPU and memory come from wherever you pointed `SAMPLE`,** and the default is
`none` — a plain run records neither. Disk and network I/O are collected by
nothing.

### 5.2 Bringing runs back

The load generator usually runs elsewhere. This copies whole run directories
across, `results.jtl` included — that is the evidence, `report.md` is only
arithmetic done on it.

```bash
make fetch-results REMOTE=user@host:/path/to/benchmark/results
```

Runs are named by timestamp, so nothing collides and re-running only brings
across what is missing.

```bash
make results                    # one line per run
make report                     # the newest in full
make report RUN=<directory>     # a particular one
```

### 5.3 Tidying up

`clean` does not touch runs or payloads. `clean-results` lists what it will
delete and asks first.

```bash
make clean
make clean-results
```

---

## Reference

### MandiPrice

Commodity prices from agricultural markets, served by Agmarknet. One catalog per
state, one resource per **market-commodity pair**.

The pairing matters:

- a market trades 1 to 80 commodities, median 4
- one resource per market made every resource a different size — one discover
  response came back 5 MB and the next 1 MB, from queries matching the same
  number of things
- one commodity per resource keeps them within about 2% of each other

```
derived 9 catalog(s) from state_name
1002 payload(s), 9 catalog(s), 100000 resources from 19135 real
market-commodity pairs (x5.23), 135.8 MB
skipped 2221 market(s) with no commodities recorded in the mapping
skipped 583 market(s) with no coordinates
skipped 9 market(s) whose coordinates are not plausible
```

**Nine catalogs, not thirty-six.** Agmarknet serves master data for 36 states
but a commodity mapping for only 9: Maharashtra, Tamil Nadu, Madhya Pradesh,
Uttar Pradesh, Karnataka, Chhattisgarh, Meghalaya, Andhra Pradesh and Bihar. The
rest contribute nothing and get no empty catalog.

- `targetResources` in `config/publish.yaml` sets the total
- real pairs are repeated to reach it, each state taking a proportional share
- `realPairs` in the manifest says how far the real data was stretched — read it
  before quoting a result
- the skips are the source data, not a bug: missing coordinates, or impossible
  ones such as a longitude of 703620 or a market called "Testing". Dropped and
  named rather than corrected

**Discover queries** carry a commodity filter and a spatial constraint together,
never one alone. Every query is distinct, and every one returns about the same
number of resources — the generator reads the published resources and places the
circle's edge between the last one it wants and the first it does not.

```
20000 queries from 636 border pairs
    S_DWITHIN + filter       9768
    S_INTERSECTS + filter    10232
    matches per query        min 67, median 76, max 83
    states per query         1:0  2:16070  3+:3930
    distinct queries         20000 of 20000
```

The remaining spread is the data's: copies of one market share its coordinates,
so reachable counts step by about ten. `matches.tolerance` absorbs that.

**How many queries do you need?** Requests sent is roughly
`threads / latency x duration` — 20 threads for 20 minutes at 100 ms is about
240,000. JMeter recycles the list, so 20,000 queries means each is re-asked
about a dozen times, far apart. Raise `queries` in the config to widen that; it
costs about a second per thousand.

**Select requests** each name one published resource: one market, one commodity
that market really trades.

**What the metadata holds** — written by [3.1](#31-refetch-the-provider-metadata)
to `data/mandiPrice-metadata/mandi-metadata.json`: states, districts, markets,
commodities, and which commodities each market trades. The last of those comes
from a mapping endpoint, one call per state, over a date window.

- all-India yields about 19k real pairs against 10k for five states, which
  halves how far the data is stretched — hence `STATES=''`
- narrow it with `STATES='Karnataka,Kerala'`
- catalogs follow whatever was fetched: `config/publish.yaml` lists no entries,
  so one catalog is derived per state present in the metadata

**Known quirks**

**Coverage is uneven.** Gujarat has no mapping data at all, which is why Madhya
Pradesh is used instead. Andhra Pradesh has four markets.

**Two code spaces.** A commodity's `code` in a payload is `commodity_id`, not
`agm_commodity_code` — they differ for 531 of 560 commodities. Tomato is 65 and
78. The same applies to districts and states: the `agm_*_code` columns are not
the ones a payload uses.

---

### Adding a capability

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
make discover CAPABILITY=<Name> URL=https://<host>
```

The runner, the JMeter plans, the sampler and the report do not change. A plan
posts whatever files the payload directory holds; it never knows which capability
it is running.
