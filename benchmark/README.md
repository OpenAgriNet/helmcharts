# Benchmark

Load tests for the OAN network layer: how much it handles, how fast it answers,
and what it costs in CPU and memory.

Build the payloads once, then run.

```bash
make dependency-check                    # is everything installed?
make data                                # build the payloads, about 45 seconds
make publish  URL=http://<host>:<port>
make discover URL=http://<host>:<port>
make select   URL=http://<host>:<port>
```

`make data` produces 100,000 resources and 20,000 discover queries, roughly
150 MB. That is too much to keep in a public repository, so it is generated
rather than committed — but it is deterministic, and `make verify-data` proves
what you built against the digests in `data/CHECKSUMS`.

`make` on its own prints the menu.

## What you need

| | Why |
|---|---|
| Go 1.22+ | builds the data tools |
| JMeter 5.6+ | drives the load, must be on your PATH |
| `kubectl` + metrics-server, or Docker | reads CPU and memory during a run |
| `curl`, `python3` | baseline timing, reading the payload manifest |

No JMeter plugins needed.

## Commands

Every command, with everything it accepts. Only `URL` is required anywhere;
all the rest have defaults.

**Check the machine is ready.** Takes nothing.

```bash
make dependency-check
```

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

**Tidy up.** Both take nothing. `clean` does not touch runs or payloads;
`clean-results` lists what it will delete and asks first.

```bash
make clean
make clean-results
```

## Settings

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
| select | consumer → network → provider → the upstream provider |

Every adapter reads the registry to verify its caller, so the registry and its
database sit in the path of all three.

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

## What a run produces

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
| `resources.csv` | CPU and memory samples |
| `progress.log` | the progress lines, every `PROGRESS_INTERVAL` seconds |
| `html/index.html` | JMeter's dashboard, with charts |
| `run.env` | the settings this run used |

Runs are never deleted for you. `make clean` only clears the build cache;
`make clean-results` asks before deleting.

### Reading the numbers

**Response time includes the network.** The load generator sits outside the
cluster. Pass `HEALTH=` and the report records a baseline round trip, so you can
tell a slow service from a distant one.

**Throughput is what was achieved, not what was asked for.** Threads wait for a
reply before sending again, so a slow service receives less traffic.

**Peak CPU is the highest value seen**, at your sampling interval. A shorter
spike is missed.

**A 200 does not mean publish succeeded.** Discovery returns 200 with per-catalog
verdicts in the body. The error rate counts HTTP failures only.

**The dashboard has no CPU or memory yet.** This stack's collector does not
collect container metrics. The report's figures come from the harness sampling
`kubectl top` directly. Disk and network I/O are collected nowhere.

## The data

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

## Layout

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

Nothing environment-specific is committed. The target is a `URL=` argument, pods
to sample are `NAMESPACE=` and `SELECTOR=`, and the report hides the hostname
unless you pass `--record-host`.

---

# MandiPrice

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

# Adding a capability

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
