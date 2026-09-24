# Observability dashboards — what to build, and how

Companion to `QUERIES.md`, which holds the SQL. This file is the *shape*: which
dashboards are worth having for this stack, which are possible with the
telemetry that exists today, and step-by-step instructions for building the one
that earns its place first.

Verified against the running stack on 2026-09-22 — Grafana **13.2.2**,
`grafana-clickhouse-datasource` **4.21.3**, ClickHouse database `otel`.

---

## Part 1 — The dashboard catalogue

Seven candidates, ordered by what they cost to build against what they answer.
The split that matters is the last column: three of them cannot be built today,
and the reason is never Grafana.

| # | Dashboard | Answers | Status |
|---|---|---|---|
| 1 | **Service Overview (RED)** | Is discovery-service up, fast and erroring? | **Build now** — full data |
| 2 | **Discovery Quality** | Are we answering *well*, not just answering? | **Build now** — full data |
| 3 | **Request Troubleshooting** | What happened to *this one* transaction? | **Build now** — full data |
| 4 | **Logs Console** | What did any container say? | **Build now** — full data |
| 5 | **Telemetry Pipeline & Cost** | Is the observability stack itself healthy, and what is it costing? | **Build now** — full data |
| 6 | **Node Saturation & Dependencies** | Is anything about to fall over? | **Partial** — one instrument exists |
| 7 | **Network-wide (cross-participant)** | Where did the transaction spend its time across four layers? | **Blocked** — not a Grafana problem |

### 1. Service Overview (RED) — build this first

Rate, errors, duration, plus the one quality number that belongs on the front
page. This is the dashboard someone opens when a phone rings, and it is what
Part 2 builds.

Panels: request rate by action · error rate % · latency p50/p95/p99 · responses
by HTTP status · error events by type · recent failures · empty-answer share.

**One design constraint, and it is the whole reason this needs building by hand
rather than importing a community OTel dashboard:** `StatusCode` is `Unset` on
almost every span in this stack, including every 400. The envelope check
rejects a malformed request and the *span* succeeds. Any imported dashboard
keyed on `StatusCode = 'Error'` will show a flat zero error rate through an
outage. Error panels here key on `SpanAttributes['http.status_code']` and on the
`error` span event instead.

### 2. Discovery Quality

The questions the network actually asks, which RED cannot answer: `result.empty`
share, what schema types are being requested, which providers answered, and
whether retrieval ran degraded.

The important panel is **empty-answer share**. A discover that matches nothing
returns 200 in single-digit milliseconds — it is indistinguishable from a
*healthy* request on every RED panel, and it is the most likely way this service
fails in production. Right now it sits at **100%** in this stack, because no
provider has published a catalog.

Second most important: **`retrieval.modes_degraded`**. A mode listed there means
the answer came back on a fallback path — a silent quality drop that is not an
error anywhere.

### 3. Request Troubleshooting

A `transaction_id` textbox variable at the top, three panels below: every span
for that transaction, every log line for it, and the trace waterfall. Reached by
a data link from the "recent failures" table on dashboard 1, so the path from
*something is wrong* to *this specific request is why* is two clicks.

This is the dashboard that makes the other ones worth having — a spike you
cannot drill into is a spike you argue about.

### 4. Logs Console

`otel_logs` holds stdout from **every** container — Keycloak, both Postgres
instances, the registry, the mocks, Grafana itself. A service dropdown, a
severity filter and a full-text box replaces `docker logs` across eleven
containers with one searchable window, and it works retroactively, which
`docker logs -f` does not.

Caveat worth putting in the panel description: only Go services have a parsed
`SeverityText`. Keycloak and Postgres lines arrive with `SeverityText = ''`, so
a severity filter silently hides them. Filter on `SeverityNumber >= 13` *or*
`SeverityText = ''` if you want everything.

### 5. Telemetry Pipeline & Cost

Freshness per signal, rows ingested per interval, and on-disk bytes per table.

Small, and the highest-leverage panel on it is **freshness**: it is what
distinguishes "traffic stopped" from "the collector stopped", and those two look
identical on every other dashboard in this list. The collector's `start_at: end`
means anything written while it is down is gone, so a stale freshness number is
also a data-loss window.

The cost half matters because ingestion is unsampled with a 720h TTL
(`config/otel-collector/config.yaml`). Currently trivial — 171 KiB of logs — but
the panel is cheaper to build now than the conversation is later.

### 6. Node Saturation & Dependencies — partial

Only one instrument exists: `pgxpool.empty_acquire` and its companion
wait-time counter. It is a genuinely good one — acquires that had to *wait*
because the pool was empty, which rises before anything fails and is invisible
to every layer outside the process — but it is one number, not a dashboard.

To fill this out, the gap is collector-side, not code-side:

- **Postgres internals** — add a `postgresql` receiver or run `postgres_exporter`
  and scrape it with the `prometheus` receiver. Gets connections, locks, cache
  hit ratio, replication lag.
- **Container CPU/memory** — add the `docker_stats` receiver to
  `config/otel-collector/config.yaml`. Gets per-container resource use without
  touching any service.
- **Host** — the `hostmetrics` receiver, if the box itself is in scope.

None of these need a code change in any service. They are three receiver blocks
in one YAML file, and they populate `otel_metrics_gauge` and
`otel_metrics_histogram`, both of which are empty today.

### 7. Network-wide (cross-participant) — blocked

The dashboard everyone wants: one transaction across experience → network →
provider, with per-hop latency.

It cannot be built, and Grafana is not the reason. Two upstream facts:

- **The adapters are not running in this stack.** Only core and observability
  profiles are up, so `provider-adapter`, `network-adapter` and
  `consumer-adapter` emit nothing.
- **Even running, nothing would stitch.** beckn-onix extracts `traceparent` on
  inbound and injects it on *no* outbound call, so every participant's span is
  the root of its own trace. `beckn.transactionId` is the only available join,
  which gives a *table* of hops but never a waterfall.

The fix is upstream (`discovery-service/docs/design/opentelemetry.md`, items
**U1**–**U3** and open question 4): an `otelhttp` round-tripper on onix's
outbound leg. Until then, the honest version of this dashboard is a table keyed
on `beckn.transactionId`, and it is worth building *as* a table rather than
waiting.

### Also considered, and deliberately not on the list

- **An SLO / error-budget dashboard.** Needs a served-request objective, and
  none is defined (OP9 in the design doc). "p95 is 300 ms" with no target is a
  number, not a verdict. Build it the day someone commits to a target.
- **Per-provider catalog freshness.** Struck as OP6 upstream, and it needs
  publish traffic this stack has almost none of (one `publish` span, ever).
- **A liveness/uptime dashboard.** kubelet and kube-state-metrics already answer
  it in the deployed environment, and a self-reported gauge lies in exactly the
  outage it exists to catch. Deliberately nothing here.

---

## Part 2 — Building the Service Overview dashboard

> **This is already built and provisioned.** Two dashboards live in
> `provisioning/dashboards/json/` and appear in the **OAN** folder on boot:
>
> | Dashboard | URL |
> |---|---|
> | OAN Discovery — Service Overview | http://127.0.0.1:8085/d/oan-discovery-overview |
> | OAN Discovery — Request Troubleshooting | http://127.0.0.1:8085/d/oan-discovery-troubleshoot |
>
> The steps below are how they were built, and what to follow to change a panel
> or add one. Since `allowUiUpdates: false`, edits made in the browser are
> overwritten on the next 30-second scan — iterate in the UI if that is easier,
> then copy the JSON model back into the file.

Two routes. **Route B is the one to use** — a dashboard built by clicking lives
only in the `grafana-data` volume, so `make destroy` deletes it and no one else
on the team ever gets it. Route A is how you iterate before committing.

### Step 0 — Pin the datasource UID — **already done, and it has a trap**

A dashboard refers to its datasource by `uid`. The datasource yaml originally
set none, so Grafana generated one (`PDEE91DDB90597936` on this machine) — which
would make every committed dashboard machine-specific. `clickhouse.yaml` now
pins `uid: clickhouse`, so nothing more is needed here.

**Read this before pinning a uid on any other datasource.** Changing the uid of
a datasource Grafana has *already* provisioned is not an update it can perform:
it looks the datasource up by uid, does not find it, and the provisioning module
fails. In Grafana 13 that failure is fatal and cascading — it takes down
`RemoteCache`, `RenderingService`, `DashboardUpdater` and the HTTP server with
it, and the container enters a restart loop that logs only:

```
Failed to provision data sources  error="Datasource provisioning error: data source not found"
```

The UI never comes up, so there is nothing to click to fix it. The fix is to
delete the old row before the new one is written, which is why the yaml carries
a `deleteDatasources` block:

```yaml
deleteDatasources:
  - name: ClickHouse
    orgId: 1
```

It runs before the datasource below it is created, so the uid change is
idempotent, and it costs nothing on a fresh stack where there is no row to
delete. Leave it in place.

### Step 1 — Create the dashboard

Grafana → **Dashboards → New → New dashboard → Settings** (gear icon):

| Setting | Value | Why |
|---|---|---|
| Title | `OAN Discovery — Service Overview` | |
| Tags | `oan`, `discovery`, `clickhouse` | Makes the folder navigable once there are five of these |
| Folder | `OAN` (create it) | |
| Time range | `now-6h` to `now` | **Not the default 6h-with-no-thought** — trace volume in this stack is bursty; a 1h default routinely shows an empty dashboard and reads as an outage |
| Refresh | `30s` | |
| Graph tooltip | **Shared crosshair** | Hovering a latency spike highlights the same instant on the error panel. This single setting is most of what makes a dashboard feel built rather than assembled |

### Step 2 — Add the variables

**Settings → Variables → New variable.** Three of them:

**`action`** — type *Query*, datasource *ClickHouse*, Multi-value ✓, Include All ✓:

```sql
SELECT DISTINCT SpanAttributes['beckn.action']
FROM otel.otel_traces
WHERE SpanAttributes['beckn.action'] != ''
```

**`service`** — type *Query*, Multi-value ✓, Include All ✓:

```sql
SELECT DISTINCT ServiceName FROM otel.otel_logs ORDER BY ServiceName
```

**`transaction_id`** — type *Textbox*, default empty. This is what the drill-down
link writes into.

Wire `action` into panels with `$__conditionalAll`, so selecting *All* does not
generate an `IN ('All')` that matches nothing:

```sql
AND $__conditionalAll(SpanAttributes['beckn.action'] IN ($action), $action)
```

### Step 3 — The headline row (four stat tiles)

Add four **Stat** panels, each `w=6 h=4`, so they sit in one row across the top.
For each: paste the SQL into the panel's *SQL Editor* tab (not the builder),
then set the options in the right-hand pane.

**Tile 1 — Requests.** Unit *short*, colour mode *Value*, graph mode *Area*
(the sparkline is what makes a stat tile worth more than a number).

```sql
SELECT count() AS requests
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp) AND ServiceName = 'discovery-service' AND SpanKind = 'Server'
```

**Tile 2 — Error rate.** Unit *Percent (0-100)*, decimals 1, thresholds:
green base, **yellow at 1**, **red at 5**.

```sql
SELECT 100 * countIf(toUInt16OrZero(SpanAttributes['http.status_code']) >= 400 OR StatusCode = 'Error') / count() AS error_pct
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp) AND ServiceName = 'discovery-service' AND SpanKind = 'Server'
```

**Tile 3 — p95 latency.** Unit *milliseconds (ms)*, thresholds: green base,
**yellow at 300**, **red at 1000**.

```sql
SELECT quantile(0.95)(Duration / 1e6) AS p95_ms
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp) AND ServiceName = 'discovery-service' AND SpanKind = 'Server'
```

**Tile 4 — Empty answers.** Unit *Percent (0-100)*, thresholds: green base,
**yellow at 10**, **red at 50**.

```sql
SELECT 100 * countIf(SpanAttributes['result.empty'] = 'true') / countIf(SpanAttributes['result.empty'] != '') AS empty_pct
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp) AND ServiceName = 'discovery-service' AND SpanAttributes['beckn.action'] = 'discover'
```

Give this one a **panel description** (the `i` tooltip): *"Discovers that matched
nothing. Returns 200 in single-digit ms — invisible on every other panel here."*
Panels that explain themselves are the difference between a dashboard people
trust and one they ask you about.

### Step 4 — Traffic & latency row

**Add → Row**, title `Traffic & Latency`. Two **Time series** panels, `w=12 h=8`.

**Request rate by action.** Legend *Bottom / Table* with `Last` and `Max`
calcs; **Min interval `1m`** under Query options — without it, a 15-minute
window produces one-second buckets and the panel turns into confetti.

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  SpanAttributes['beckn.action'] AS action,
  count() AS requests
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service' AND SpanKind = 'Server'
  AND $__conditionalAll(SpanAttributes['beckn.action'] IN ($action), $action)
GROUP BY time, action
ORDER BY time
```

**Latency percentiles.** Unit *ms*, log scale off, fill opacity 10.

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  quantile(0.50)(Duration / 1e6) AS p50,
  quantile(0.95)(Duration / 1e6) AS p95,
  quantile(0.99)(Duration / 1e6) AS p99
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service' AND SpanKind = 'Server'
GROUP BY time
ORDER BY time
```

Traffic here is bursty enough that gaps read as zeros. If you want true zeros
on a **single-series** panel, ClickHouse fills them server-side:

```sql
ORDER BY time WITH FILL FROM $__fromTime TO $__toTime STEP 60
```

Do **not** use `WITH FILL` on the multi-series panel above — it fills the time
column only, and you get rows with an empty `action` label.

### Step 5 — Failures row

**Add → Row**, title `Failures`. Collapsed by default is wrong here; leave it
open.

**Error events by type** — Time series, `w=12 h=8`, draw style *Bars*, stacking
*Normal*:

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  ev.2['type'] AS error_type,
  count() AS errors
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp) AND ev.1 = 'error'
GROUP BY time, error_type
ORDER BY time
```

**Errors by type and code** — Table, `w=12 h=8`, cell type *Coloured background*
on the `errors` column:

```sql
SELECT
  ev.2['type'] AS error_type,
  ev.2['code'] AS error_code,
  ev.2['path'] AS path,
  count() AS errors
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp) AND ev.1 = 'error'
GROUP BY error_type, error_code, path
ORDER BY errors DESC
```

**Recent failed requests** — Table, `w=24 h=8`:

```sql
SELECT
  Timestamp AS time,
  SpanAttributes['beckn.action'] AS action,
  SpanAttributes['http.status_code'] AS status,
  ev.2['type'] AS error_type,
  ev.2['code'] AS error_code,
  ev.2['msg'] AS message,
  SpanAttributes['beckn.transactionId'] AS transaction_id,
  TraceId
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp) AND ev.1 = 'error'
ORDER BY Timestamp DESC
LIMIT 100
```

**This is the panel that needs the drill-down link**, and it needs it on *two*
columns. Field override on `transaction_id`, and a second on `TraceId`, both
carrying the same URL:

```
/d/oan-discovery-troubleshoot/oan-discovery-troubleshoot
  ?var-transaction_id=${__data.fields.transaction_id}
  &var-trace_id=${__data.fields.TraceId}
  &${__url_time_range}
```

Two ids, not one, and the reason is specific: **an envelope-rejected request has
no `beckn.transactionId` at all.** The context never parsed — that is what it was
rejected for — so the attribute is an empty string on precisely the rows this
table contains. A drill-down keyed on the transaction id alone lands on an empty
dashboard for every 400, which is the most common thing anyone will click here.
`TraceId` always exists, so it is the fallback, and the target dashboard accepts
either.

`${__url_time_range}` matters for the same class of reason — without it the
drill-down opens on its own default window and shows nothing.

### Step 6 — Discovery quality row

**Add → Row**, title `Discovery Quality`. Three panels, `w=8 h=8`.

**Empty-answer share** — Time series, unit *Percent (0-100)*, max 100, a
**dashed red threshold line at 50**:

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  100 * countIf(SpanAttributes['result.empty'] = 'true') / countIf(SpanAttributes['result.empty'] != '') AS empty_pct
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service' AND SpanAttributes['beckn.action'] = 'discover'
GROUP BY time
ORDER BY time
```

**What is being asked for** — Table:

`beckn.schemaType` arrives as a serialised array —
`["openagrinet:MandiPrice"]` — so strip the brackets and the prefix, or the
table column is twice as wide as it needs to be and reads as JSON:

```sql
SELECT
  replaceRegexpAll(SpanAttributes['beckn.schemaType'], '[\\[\\]"]|openagrinet:', '') AS schema_type,
  count() AS requests,
  countIf(SpanAttributes['result.empty'] = 'true') AS empty_results,
  round(100 * countIf(SpanAttributes['result.empty'] = 'true') / count(), 1) AS empty_pct
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp) AND SpanAttributes['beckn.schemaType'] != ''
GROUP BY schema_type
ORDER BY requests DESC
```

**Retrieval modes degraded** — Table:

```sql
SELECT
  ev.2['retrieval.modes_run'] AS modes_run,
  ev.2['retrieval.modes_degraded'] AS modes_degraded,
  count() AS requests
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp) AND ev.1 = 'retrieval_info'
GROUP BY modes_run, modes_degraded
ORDER BY requests DESC
```

### Step 7 — Saturation & pipeline row (collapsed)

**Add → Row**, title `Saturation & Pipeline`, **collapsed ✓** — this is the row
you open when the top of the dashboard has already told you something is wrong.

**Pool acquire-waits** — Time series. Cumulative counters, so the panel needs the
per-interval increase, not the raw value:

```sql
SELECT
  time,
  greatest(0, cum - any(cum) OVER (ORDER BY time ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING)) AS empty_acquires
FROM (
  SELECT $__timeInterval(TimeUnix) AS time, max(Value) AS cum
  FROM otel.otel_metrics_sum
  WHERE $__timeFilter(TimeUnix) AND MetricName = 'pgxpool.empty_acquire'
  GROUP BY time
)
ORDER BY time
```

**Telemetry freshness** — Table. The panel that tells you whether to believe the
rest of the dashboard:

```sql
SELECT 'traces' AS signal, max(Timestamp) AS last_seen, count() AS rows_in_range
FROM otel.otel_traces WHERE $__timeFilter(Timestamp)
UNION ALL
SELECT 'logs', max(Timestamp), count() FROM otel.otel_logs WHERE $__timeFilter(Timestamp)
UNION ALL
SELECT 'metrics', max(TimeUnix), count() FROM otel.otel_metrics_sum WHERE $__timeFilter(TimeUnix)
```

### Step 8 — Logs row (collapsed)

**Add → Row**, title `Logs`, collapsed ✓. One **Logs** panel, `w=24 h=12`:

```sql
SELECT
  Timestamp AS time,
  ServiceName AS service,
  SeverityText AS level,
  Body AS body,
  TraceId AS trace_id
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND $__conditionalAll(ServiceName IN ($service), $service)
ORDER BY Timestamp DESC
LIMIT 500
```

Panel options: *Time* ✓, *Wrap lines* ✓, *Order: Newest first*.

### Step 9 — Commit it (Route B)

What makes this survive `make destroy`, and reach anyone else.

1. **Dashboard settings → JSON Model**, copy the whole document.
2. Save it as
   `config/grafana/provisioning/dashboards/json/oan-discovery-overview.json`.
3. In that JSON, set `"uid": "oan-discovery-overview"` and strip the top-level
   `"id"` field (set it to `null`) — a provisioned dashboard must not carry the
   database id from the instance you built it on.
4. Confirm every panel's datasource reads
   `{"type": "grafana-clickhouse-datasource", "uid": "clickhouse"}`. If you
   skipped Step 0, this is where it bites.
5. Create the provider file
   `config/grafana/provisioning/dashboards/dashboards.yaml`:

```yaml
apiVersion: 1

providers:
  - name: oan
    orgId: 1
    folder: OAN
    type: file
    disableDeletion: false
    # Dashboards are reloaded from disk, so edits in the UI are overwritten on
    # the next scan. That is the point: the file is the source, the UI is a
    # scratchpad. allowUiUpdates would invert it.
    allowUiUpdates: false
    updateIntervalSeconds: 30
    options:
      path: /etc/grafana/provisioning/dashboards/json
      foldersFromFilesStructure: false
```

6. `docker compose restart grafana`.

No compose change is needed — `docker-compose.yml` already mounts the whole
`./config/grafana/provisioning` directory read-only, so both new files are
picked up on restart.

### Step 10 — Verify it actually shows data

The failure mode from earlier today was a correct dashboard over an empty time
window. Check the data before you debug the panels:

```bash
docker exec clickhouse clickhouse-client --query \
  "SELECT max(Timestamp), dateDiff('minute', max(Timestamp), now()) AS age_min FROM otel.otel_traces"
```

If `age_min` is large, nothing is generating traffic — the adapters are not
running in this stack, so the only span source is a direct call:

```bash
curl -X POST http://127.0.0.1:8090/discover \
  -H 'Content-Type: application/json' \
  --data @<a discover body from api-collection/OpenAgriNet.api-collection.json>
```

Give the collector ~10s to flush its batch, then reload the dashboard.
