# Checking one service in Explore — logs, traces, metrics

Copy-paste queries for **Explore**, where you check a single service by hand
rather than watching a dashboard. Every one was run against this stack.

Explore is at **http://127.0.0.1:8085/explore**.

**Two things about Explore that differ from a dashboard panel**, and both bite
silently:

- **Dashboard variables do not exist here.** `$service`, `$__conditionalAll(...)`
  and `$transaction_id` are dashboard features — in Explore they are not
  substituted and the query either errors or matches nothing. Every query below
  has the service name written out; change the one quoted string.
- **`$__timeFilter(...)` and `$__timeInterval(...)` do work**, and follow the
  time picker top-right. Set it to **Last 24 hours** to start. Trace volume here
  is bursty and a short window routinely shows an empty result that looks like a
  broken query.

In the ClickHouse query editor, switch the editor toggle to **SQL Editor**
(rather than Builder), and set **Query Type** to match what you are asking for —
*Logs* for the log queries, *Table* for tables, *Time Series* for anything
selecting a `time` column. The wrong query type is the usual reason a correct
query renders as an unreadable table.

---

## Step 1 — What does each service actually have?

**Run this first.** Most services in this stack have logs and nothing else, so
without it you will run a trace query against Keycloak, get nothing, and think
the query is wrong.

```sql
SELECT service, sum(logs) AS logs, sum(traces) AS traces, sum(metrics) AS metric_points
FROM (
  SELECT ServiceName AS service, count() AS logs, 0 AS traces, 0 AS metrics
  FROM otel.otel_logs WHERE $__timeFilter(Timestamp) GROUP BY service
  UNION ALL
  SELECT ServiceName, 0, count(), 0
  FROM otel.otel_traces WHERE $__timeFilter(Timestamp) GROUP BY ServiceName
  UNION ALL
  SELECT ServiceName, 0, 0, count()
  FROM otel.otel_metrics_sum WHERE $__timeFilter(TimeUnix) GROUP BY ServiceName
)
GROUP BY service
ORDER BY logs DESC
```

Over the last 24 hours it returns this — which is the map for everything below:

| ServiceName | logs | traces | metrics |
|---|---:|---:|---:|
| `grafana` | 10801 | — | — |
| `sunbird-registry-keycloak` | 468 | — | — |
| `sunbird-registry-service` | 233 | — | — |
| `sunbird-registry-postgres` | 72 | — | — |
| `discovery-postgres` | 60 | — | — |
| **`discovery-service`** | **50** | **42** | **544** |
| `mock-agmarknet` | 4 | — | — |
| `mock-imd` | 3 | — | — |
| `mock-agmarknet-token` | 1 | — | — |

**`discovery-service` is the only service with all three signals**, and that is
not a gap in the collector — it is the only service in this stack that is
instrumented. Everything else is logs-only because its logs come from the
`filelog` receiver scraping container stdout, which needs no cooperation from
the service. Traces and metrics need the service to emit OTLP, and only
discovery-service does.

`grafana` dominating the log count is Grafana logging its own query activity,
which the collector then ships back into ClickHouse. Worth knowing before it
looks like an incident.

---

## Logs — any service

Query Type **Logs**. Change the service name on the one line.

```sql
SELECT
  Timestamp AS timestamp,
  Body AS body,
  SeverityText AS level,
  LogAttributes['trace_id'] AS trace_id
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
ORDER BY Timestamp DESC
LIMIT 200
```

Service names to substitute: `discovery-service`, `discovery-postgres`,
`sunbird-registry-service`, `sunbird-registry-keycloak`,
`sunbird-registry-postgres`, `mock-imd`, `mock-agmarknet`,
`mock-agmarknet-token`, `grafana`.

### Errors and warnings only

```sql
SELECT Timestamp AS timestamp, Body AS body, SeverityText AS level, ServiceName
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SeverityNumber >= 13
ORDER BY Timestamp DESC
LIMIT 200
```

**This filter hides most of the stack**, deliberately or not: only Go services
have a parsed `SeverityText`/`SeverityNumber`. Keycloak, both Postgres instances
and the registry arrive as plain text with severity `0`, so they never match.
For those, search the body instead:

```sql
SELECT Timestamp AS timestamp, Body AS body, ServiceName
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'sunbird-registry-keycloak'
  AND (positionCaseInsensitive(Body, 'error') > 0
       OR positionCaseInsensitive(Body, 'exception') > 0
       OR positionCaseInsensitive(Body, 'fatal') > 0)
ORDER BY Timestamp DESC
LIMIT 200
```

### Full-text across every service at once

The `docker logs` replacement — searches all eleven containers, and works
retroactively, which `docker logs -f` does not.

```sql
SELECT Timestamp AS timestamp, Body AS body, SeverityText AS level, ServiceName
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND positionCaseInsensitive(Body, 'connection refused') > 0
ORDER BY Timestamp DESC
LIMIT 200
```

### Structured fields from a Go service

discovery-service logs zap JSON, which `transform/logs` parsed into attributes —
so the fields are columns, not text to grep.

```sql
SELECT
  Timestamp AS time,
  SeverityText AS level,
  LogAttributes['msg'] AS msg,
  LogAttributes['action'] AS action,
  LogAttributes['status'] AS status,
  toFloat64OrZero(LogAttributes['duration_ms']) AS duration_ms,
  LogAttributes['error_type'] AS error_type,
  LogAttributes['transaction_id'] AS transaction_id,
  LogAttributes['trace_id'] AS trace_id
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND LogAttributes['msg'] != ''
ORDER BY Timestamp DESC
LIMIT 200
```

To see every field a service actually sets, before writing the query above:

```sql
SELECT arrayJoin(mapKeys(LogAttributes)) AS field, count() AS rows
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp) AND ServiceName = 'discovery-service'
GROUP BY field
ORDER BY rows DESC
```

---

## Traces — `discovery-service` only

Query Type **Table**.

```sql
SELECT
  Timestamp AS time,
  SpanName AS span,
  round(Duration / 1e6, 2) AS duration_ms,
  SpanAttributes['http.status_code'] AS status,
  SpanAttributes['beckn.action'] AS action,
  SpanAttributes['result.empty'] AS empty_result,
  SpanAttributes['beckn.transactionId'] AS transaction_id,
  TraceId
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
ORDER BY Timestamp DESC
LIMIT 100
```

### Failures only

Note what this does **not** filter on. `StatusCode` is `Unset` on nearly every
failed span here — an envelope rejection returns 400 while the span itself
succeeds — so `StatusCode = 'Error'` finds almost nothing.

```sql
SELECT
  Timestamp AS time,
  SpanName AS span,
  SpanAttributes['http.status_code'] AS status,
  ev.2['type'] AS error_type,
  ev.2['code'] AS error_code,
  ev.2['msg'] AS message,
  ev.2['path'] AS path,
  TraceId
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND ev.1 = 'error'
ORDER BY Timestamp DESC
LIMIT 100
```

### Everything recorded about one span

Paste a `TraceId` from either query above.

```sql
SELECT
  'attribute' AS kind,
  arrayJoin(mapKeys(SpanAttributes)) AS name,
  SpanAttributes[arrayJoin(mapKeys(SpanAttributes))] AS value
FROM otel.otel_traces
WHERE TraceId = 'PASTE_TRACE_ID_HERE'
```

and its events, where the interesting half lives:

```sql
SELECT
  ev.1 AS time,
  ev.2 AS event,
  arrayStringConcat(arrayMap(k -> concat(k, ' = ', ev.3[k]), arraySort(mapKeys(ev.3))), '\n') AS attributes
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Timestamp, Events.Name, Events.Attributes) AS ev
WHERE TraceId = 'PASTE_TRACE_ID_HERE'
ORDER BY time
```

### Logs for that same request

**Join on `LogAttributes['trace_id']`, not the `TraceId` column.** These logs
arrive through `filelog` reading container stdout, not over OTLP, so the
collector never fills the native column — it is empty on 11600 of 11601 rows.
The id is a field inside the zap JSON.

```sql
SELECT Timestamp AS time, SeverityText AS level, LogAttributes['msg'] AS msg, Body
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND LogAttributes['trace_id'] = 'PASTE_TRACE_ID_HERE'
ORDER BY Timestamp
```

---

## Metrics — `discovery-service` only

Query Type **Time Series**.

```sql
SELECT
  $__timeInterval(TimeUnix) AS time,
  MetricName AS metric,
  max(Value) AS value
FROM otel.otel_metrics_sum
WHERE $__timeFilter(TimeUnix)
  AND ServiceName = 'discovery-service'
GROUP BY time, metric
ORDER BY time
```

Two instruments exist — `pgxpool.empty_acquire` and
`pgxpool.empty_acquire_wait_time` — and both are **cumulative monotonic
counters**, so the raw value only ever climbs. What you usually want is the
per-interval increase:

```sql
SELECT
  time,
  greatest(0, cum - any(cum) OVER (ORDER BY time ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING)) AS increase
FROM (
  SELECT $__timeInterval(TimeUnix) AS time, max(Value) AS cum
  FROM otel.otel_metrics_sum
  WHERE $__timeFilter(TimeUnix) AND MetricName = 'pgxpool.empty_acquire'
  GROUP BY time
)
ORDER BY time
```

Both counters are flat at **0** in this stack — the connection pool has never had
to make a caller wait. A flat zero line is the correct output, not a broken
query.

### Which metrics exist at all

`otel_metrics_gauge` and `otel_metrics_histogram` are empty — nothing emits
either kind yet — so this only ever returns rows from `_sum` today:

```sql
SELECT 'sum' AS kind, ServiceName, MetricName, count() AS points, max(Value) AS latest
FROM otel.otel_metrics_sum WHERE $__timeFilter(TimeUnix) GROUP BY ServiceName, MetricName
UNION ALL
SELECT 'gauge', ServiceName, MetricName, count(), max(Value)
FROM otel.otel_metrics_gauge WHERE $__timeFilter(TimeUnix) GROUP BY ServiceName, MetricName
UNION ALL
SELECT 'histogram', ServiceName, MetricName, count(), max(Sum)
FROM otel.otel_metrics_histogram WHERE $__timeFilter(TimeUnix) GROUP BY ServiceName, MetricName
```

---

## All three signals for one service, in one result

Query Type **Table**. Change the service name in all three places.

```sql
SELECT * FROM (
  SELECT Timestamp AS time, 'trace' AS signal,
         concat(SpanName, ' -> ', SpanAttributes['http.status_code'],
                ' in ', toString(round(Duration / 1e6, 1)), ' ms') AS detail
  FROM otel.otel_traces
  WHERE $__timeFilter(Timestamp) AND ServiceName = 'discovery-service'

  UNION ALL

  SELECT Timestamp, 'log',
         concat(if(SeverityText = '', '-', SeverityText), ': ',
                if(LogAttributes['msg'] != '', LogAttributes['msg'], substring(Body, 1, 120)))
  FROM otel.otel_logs
  WHERE $__timeFilter(Timestamp) AND ServiceName = 'discovery-service'

  UNION ALL

  SELECT toDateTime64(bucket, 9), 'metric', arrayStringConcat(groupArray(kv), '   ')
  FROM (
    SELECT toStartOfInterval(TimeUnix, INTERVAL 300 second) AS bucket,
           concat(MetricName, ' = ', toString(argMax(Value, TimeUnix))) AS kv
    FROM otel.otel_metrics_sum
    WHERE $__timeFilter(TimeUnix) AND ServiceName = 'discovery-service'
    GROUP BY bucket, MetricName
  )
  GROUP BY bucket
)
ORDER BY time DESC
LIMIT 300
```

`ORDER BY` has to sit outside the union — inside, it binds to the last `SELECT`
only and ClickHouse fails with `Unknown expression identifier 'time'`. The
metric leg is collapsed to one row per five minutes because a metric is a level
sampled on a clock, not an event: left raw it emits a point per minute whether
or not anything happened, and outnumbers the traces it is there to give context
to.

---

## The same checks from the terminal

When Explore is more clicks than the question deserves. `FORMAT Vertical` is
worth knowing — it prints one field per line instead of a wide unreadable row.

```bash
# What each service has
docker exec clickhouse clickhouse-client --query "
  SELECT ServiceName, count() FROM otel.otel_logs GROUP BY ServiceName ORDER BY 2 DESC FORMAT PrettyCompact"

# Tail one service's logs
docker exec clickhouse clickhouse-client --query "
  SELECT Timestamp, SeverityText, Body FROM otel.otel_logs
  WHERE ServiceName = 'discovery-service' ORDER BY Timestamp DESC LIMIT 20 FORMAT Vertical"

# Recent spans
docker exec clickhouse clickhouse-client --query "
  SELECT Timestamp, SpanName, round(Duration/1e6,2) AS ms, SpanAttributes['http.status_code'] AS status, TraceId
  FROM otel.otel_traces ORDER BY Timestamp DESC LIMIT 20 FORMAT PrettyCompact"

# Is telemetry arriving at all
docker exec clickhouse clickhouse-client --query "
  SELECT 'traces' AS signal, max(Timestamp) AS last_seen, dateDiff('minute', max(Timestamp), now()) AS age_min FROM otel.otel_traces
  UNION ALL SELECT 'logs', max(Timestamp), dateDiff('minute', max(Timestamp), now()) FROM otel.otel_logs
  UNION ALL SELECT 'metrics', max(TimeUnix), dateDiff('minute', max(TimeUnix), now()) FROM otel.otel_metrics_sum
  FORMAT PrettyCompact"
```

## If a query returns nothing

In the order these actually happen here:

1. **The time range.** Last 24 hours, not last 1 hour. Traces arrive in bursts.
2. **The service has no such signal.** Run Step 1 — only `discovery-service` has
   traces or metrics.
3. **Nothing is generating traffic.** The three adapters are not running in this
   stack, so the only way to produce a span is to call discovery-service
   directly:
   ```bash
   curl -X POST http://127.0.0.1:8090/discover -H 'Content-Type: application/json' \
     --data @<a Discover body from api-collection/OpenAgriNet.api-collection.json>
   ```
   Allow ~10s for the collector to flush its batch.
4. **A dashboard variable leaked into an Explore query** — `$service` or
   `$__conditionalAll` pasted from `QUERIES.md`. Neither exists here.

---

## Reading logs, not counting them

All four below are **Query Type: Logs**, and they all put the message
**immediately after the timestamp**. That ordering is load-bearing: Grafana's
log renderer takes the **first string field in the result** as the log line,
*regardless of what it is called*. Select `ServiceName` or `SeverityText` before
`Body` and the panel renders the service name — or the word `info` — as every
log line, with the real message nowhere on screen and no error to explain it.

Everything after the body is context, shown when you expand a line.

### One service, raw

```sql
SELECT Timestamp AS timestamp, Body AS body, SeverityText AS level, ServiceName
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
ORDER BY Timestamp DESC
LIMIT 500
```

### discovery-service, readable

The raw body is zap JSON — one long line per entry, unreadable in a log view.
The collector already parsed it into attributes, so rebuild the line from the
fields that matter instead:

```sql
SELECT
  Timestamp AS timestamp,
  concat(
    LogAttributes['msg'],
    if(LogAttributes['action']      != '', concat('  action=', LogAttributes['action']), ''),
    if(LogAttributes['status']      != '', concat('  status=', LogAttributes['status']), ''),
    if(LogAttributes['duration_ms'] != '', concat('  ', LogAttributes['duration_ms'], 'ms'), ''),
    if(LogAttributes['error_code']  != '', concat('  err=', LogAttributes['error_code']), '')
  ) AS body,
  SeverityText AS level,
  LogAttributes['transaction_id'] AS transaction_id,
  LogAttributes['trace_id'] AS trace_id
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND LogAttributes['msg'] != ''
ORDER BY Timestamp DESC
LIMIT 500
```

Reads like a request log rather than a JSON dump:

```
info  request completed  status=400  5.243ms  err=CTX_MISSING_FIELD
warn  rejected request  err=CTX_MISSING_FIELD
info  request completed  action=discover  status=200  1.01ms
info  request completed  action=discover  status=200  269.315ms
```

The `AND LogAttributes['msg'] != ''` clause drops the lines that are not zap
JSON — startup banners and anything written before the logger is up. Remove it
if something is missing and you suspect it was written early.

### Every service at once

The service name has to go **into the body**: a log view shows the body, so
without the prefix a stack-wide tail is eleven containers' output with no way to
tell whose line is whose.

```sql
SELECT
  Timestamp AS timestamp,
  concat('[', ServiceName, '] ',
         if(LogAttributes['msg'] != '', LogAttributes['msg'], Body)) AS body,
  SeverityText AS level
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName != 'grafana'
ORDER BY Timestamp DESC
LIMIT 500
```

`ServiceName != 'grafana'` is doing real work — Grafana logs its own query
activity, the collector ships it back in, and it is 10801 of the ~11900 rows
here. Left in, a stack-wide tail is almost entirely Grafana describing the query
you just ran.

### Anything that looks like a problem, stack-wide

Severity alone is not enough: only Go services set one. Keycloak, both Postgres
instances and the registry arrive as plain text at severity `0`, so this matches
on the text as well.

```sql
SELECT
  Timestamp AS timestamp,
  concat('[', ServiceName, '] ', Body) AS body,
  SeverityText AS level
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ServiceName != 'grafana'
  AND (SeverityNumber >= 13
       OR positionCaseInsensitive(Body, 'error') > 0
       OR positionCaseInsensitive(Body, 'exception') > 0
       OR positionCaseInsensitive(Body, 'fatal') > 0
       OR positionCaseInsensitive(Body, 'panic') > 0)
ORDER BY Timestamp DESC
LIMIT 500
```
