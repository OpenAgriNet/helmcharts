# Grafana panel queries — ClickHouse datasource

Every query below runs against the `ClickHouse` datasource provisioned by
`provisioning/datasources/clickhouse.yaml`, on the database the OTel collector
exports to (`CLICKHOUSE_DB`, default `otel`). Tables are the ones
`config/otel-collector/config.yaml` names: `otel_logs`, `otel_traces`,
`otel_metrics_*` — created by the exporter's `create_schema: true`, so their
columns are the exporter's, not ours.

Queries are written against the running stack and were checked to return rows.
They use Grafana's ClickHouse macros, which only expand inside a panel:

| Macro | Expands to |
|---|---|
| `$__timeFilter(Timestamp)` | `Timestamp >= toDateTime(<from>) AND Timestamp <= toDateTime(<to>)` |
| `$__timeInterval(Timestamp)` | `toStartOfInterval(Timestamp, INTERVAL <panel step> second)` |
| `$__conditionalAll(cond, $var)` | `cond`, unless `$var` is `All` — then `1=1` |

Set the panel's **Format** to *Time series* where a query selects `time`, and
*Table* / *Logs* otherwise.

**In any Logs-format query, select the message immediately after the
timestamp.** Grafana's log renderer takes the **first string field** as the log
line regardless of its name, so putting `ServiceName` or `SeverityText` before
`Body` makes the panel render the service name — or the word `info` — as every
line, with the real message nowhere and no error to explain it.

## What is actually in the data

Worth knowing before adapting anything below, because several plausible columns
are empty in this stack:

- **Spans.** Only `discovery-service` emits them today (`/discover`, `/publish`
  — probes are excluded by design). `SpanName` is the route; earlier rows use
  the bare action (`discover`), newer ones the path (`/discover`), so group on
  `beckn.action` rather than `SpanName` if you want one series.
- **`StatusCode` is mostly `Unset`.** A 400 from the envelope check is a
  successful *span* — the failure is in `SpanAttributes['http.status_code']` and
  in the `error` span event. Do not build an error rate on `StatusCode`.
- **Adapters (provider/network/consumer) emit no spans here** — beckn-onix does
  not inject `traceparent` outbound, so nothing stitches across participants.
  `beckn.transactionId` is the only join key.
- **Metrics.** Exactly two, both cumulative monotonic sums from
  discovery-service: `pgxpool.empty_acquire`, `pgxpool.empty_acquire_wait_time`.
  `otel_metrics_gauge` and `otel_metrics_histogram` are empty.
- **Logs.** Every container's stdout lands in `otel_logs`. Only Go services
  (discovery-service) have a parsed `SeverityText`; Keycloak, Postgres and the
  registry arrive as plain text with `SeverityText = ''`.

---

## Traces — RED

### 1. Request rate by action (time series)

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  SpanAttributes['beckn.action'] AS action,
  count() AS requests
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanKind = 'Server'
GROUP BY time, action
ORDER BY time
```

### 2. Error rate % (time series)

Errors are HTTP status ≥ 400 or a span status of `Error` — not `StatusCode`
alone, see above.

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  100 * countIf(
    toUInt16OrZero(SpanAttributes['http.status_code']) >= 400
    OR StatusCode = 'Error'
  ) / count() AS error_pct
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanKind = 'Server'
GROUP BY time
ORDER BY time
```

### 3. Latency percentiles, ms (time series)

`Duration` is nanoseconds.

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  quantile(0.50)(Duration / 1e6) AS p50,
  quantile(0.95)(Duration / 1e6) AS p95,
  quantile(0.99)(Duration / 1e6) AS p99
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanKind = 'Server'
GROUP BY time
ORDER BY time
```

### 4. Latency by action (table)

```sql
SELECT
  SpanAttributes['beckn.action'] AS action,
  count() AS requests,
  round(avg(Duration / 1e6), 2) AS avg_ms,
  round(quantile(0.95)(Duration / 1e6), 2) AS p95_ms,
  round(max(Duration / 1e6), 2) AS max_ms
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanKind = 'Server'
GROUP BY action
ORDER BY requests DESC
```

### 5. Responses by HTTP status (time series, stacked bars)

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  SpanAttributes['http.status_code'] AS status,
  count() AS responses
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanAttributes['http.status_code'] != ''
GROUP BY time, status
ORDER BY time
```

### 6. Total requests / errors / p95 (stat row)

```sql
SELECT
  count() AS requests,
  countIf(toUInt16OrZero(SpanAttributes['http.status_code']) >= 400) AS errors,
  round(quantile(0.95)(Duration / 1e6), 1) AS p95_ms,
  uniqExact(SpanAttributes['beckn.transactionId']) AS transactions
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanKind = 'Server'
```

---

## Traces — failures

### 7. Errors by type and code (table)

The `error` span event carries `type`, `code`, `path` and `msg`.

```sql
SELECT
  ev.2['type'] AS error_type,
  ev.2['code'] AS error_code,
  ev.2['path'] AS path,
  count() AS errors
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp)
  AND ev.1 = 'error'
GROUP BY error_type, error_code, path
ORDER BY errors DESC
```

### 8. Error events over time by type (time series)

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  ev.2['type'] AS error_type,
  count() AS errors
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp)
  AND ev.1 = 'error'
GROUP BY time, error_type
ORDER BY time
```

### 9. Recent failed requests (table)

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
WHERE $__timeFilter(Timestamp)
  AND ev.1 = 'error'
ORDER BY Timestamp DESC
LIMIT 100
```

### 10. Slowest requests (table)

```sql
SELECT
  Timestamp AS time,
  SpanAttributes['beckn.action'] AS action,
  round(Duration / 1e6, 2) AS duration_ms,
  SpanAttributes['http.status_code'] AS status,
  SpanAttributes['beckn.transactionId'] AS transaction_id,
  TraceId
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanKind = 'Server'
ORDER BY Duration DESC
LIMIT 50
```

---

## Traces — discovery behaviour

### 11. Empty-answer share (time series)

`result.empty` is the "we answered, with nothing" signal — a health number that
never shows up as an error.

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  100 * countIf(SpanAttributes['result.empty'] = 'true')
      / countIf(SpanAttributes['result.empty'] != '') AS empty_pct
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanAttributes['beckn.action'] = 'discover'
GROUP BY time
ORDER BY time
```

### 12. What is being asked for (table)

```sql
SELECT
  SpanAttributes['beckn.schemaType'] AS schema_type,
  SpanAttributes['beckn.schemaContext'] AS schema_context,
  count() AS requests,
  countIf(SpanAttributes['result.empty'] = 'true') AS empty_results
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanAttributes['beckn.schemaType'] != ''
GROUP BY schema_type, schema_context
ORDER BY requests DESC
```

### 13. Which providers answered (table)

`result.provider_ids` lives on the `response_info` event as a serialised list.

```sql
SELECT
  ev.2['result.provider_ids'] AS provider_ids,
  count() AS discovers,
  round(avg(toFloat64OrZero(ev.2['result.catalog_count'])), 1) AS avg_catalogs
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp)
  AND ev.1 = 'response_info'
GROUP BY provider_ids
ORDER BY discovers DESC
```

### 14. Retrieval modes run vs degraded (table)

A mode in `modes_degraded` means the answer came back on a fallback path —
a silent quality drop.

```sql
SELECT
  ev.2['retrieval.modes_run'] AS modes_run,
  ev.2['retrieval.modes_degraded'] AS modes_degraded,
  count() AS requests
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp)
  AND ev.1 = 'retrieval_info'
GROUP BY modes_run, modes_degraded
ORDER BY requests DESC
```

### 15. Intent shape (table)

```sql
SELECT
  ev.2['intent.kinds'] AS kinds,
  ev.2['intent.filter_type'] AS filter_type,
  ev.2['intent.spatial_ops'] AS spatial_ops,
  ev.2['intent.scoped'] AS scoped,
  count() AS requests
FROM otel.otel_traces
ARRAY JOIN arrayZip(Events.Name, Events.Attributes) AS ev
WHERE $__timeFilter(Timestamp)
  AND ev.1 = 'request_info'
GROUP BY kinds, filter_type, spatial_ops, scoped
ORDER BY requests DESC
```

### 16. Unidentified senders (time series)

`sender.unidentified = true` is the size of the hole under "how many seekers" —
`sender.id` is optional and usually absent.

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  countIf(SpanAttributes['sender.unidentified'] = 'true') AS unidentified,
  countIf(SpanAttributes['sender.unidentified'] = 'false') AS identified
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND ServiceName = 'discovery-service'
  AND SpanKind = 'Server'
GROUP BY time
ORDER BY time
```

---

## One transaction, end to end

### 17. Every span for a transaction (table)

Add a dashboard **text variable** named `transaction_id`, then drill in from
panel 9 or 10 with a data link.

```sql
SELECT
  Timestamp AS time,
  ServiceName,
  SpanName,
  round(Duration / 1e6, 2) AS duration_ms,
  StatusCode,
  SpanAttributes['http.status_code'] AS http_status,
  SpanAttributes['beckn.messageId'] AS message_id,
  TraceId,
  SpanId,
  ParentSpanId
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
  AND SpanAttributes['beckn.transactionId'] = '$transaction_id'
ORDER BY Timestamp
```

### 18. Logs for the same transaction (Logs panel)

Spans and logs join on `transaction_id`, which the collector's `transform/logs`
stage lifts out of the zap JSON body.

```sql
SELECT
  Timestamp AS timestamp,
  Body AS body,
  SeverityText AS level,
  LogAttributes['msg'] AS msg,
  LogAttributes['duration_ms'] AS duration_ms,
  LogAttributes['status'] AS status,
  TraceId
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND LogAttributes['transaction_id'] = '$transaction_id'
ORDER BY Timestamp
```

### 19. Trace waterfall (Traces panel)

Set the panel's query type to **Traces → Trace ID** in the plugin's query
builder, or use the raw SQL the builder emits:

```sql
SELECT
  TraceId AS traceID,
  SpanId AS spanID,
  ParentSpanId AS parentSpanID,
  ServiceName AS serviceName,
  SpanName AS operationName,
  toFloat64(toUnixTimestamp64Milli(Timestamp)) AS startTime,
  toFloat64(Duration) / 1e6 AS duration,
  arrayMap(k -> map('key', k, 'value', SpanAttributes[k]), mapKeys(SpanAttributes)) AS tags
FROM otel.otel_traces
WHERE TraceId = '$trace_id'
ORDER BY startTime
```

---

## Logs

### 20. Log volume by service (time series, stacked)

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  ServiceName AS service,
  count() AS lines
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND $__conditionalAll(ServiceName IN ($service), $service)
GROUP BY time, service
ORDER BY time
```

Pair it with a `service` multi-value variable:

```sql
SELECT DISTINCT ServiceName FROM otel.otel_logs ORDER BY ServiceName
```

### 21. Warn/error lines over time (time series)

```sql
SELECT
  $__timeInterval(Timestamp) AS time,
  SeverityText AS level,
  count() AS lines
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND SeverityNumber >= 13
GROUP BY time, level
ORDER BY time
```

### 22. Live log stream (Logs panel)

```sql
SELECT
  Timestamp AS timestamp,
  Body AS body,
  SeverityText AS level,
  ServiceName,
  TraceId
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND $__conditionalAll(ServiceName IN ($service), $service)
ORDER BY Timestamp DESC
LIMIT 500
```

### 23. Full-text search across all container logs (Logs panel)

Add a text variable `search` (empty means everything).

```sql
SELECT
  Timestamp AS timestamp,
  Body AS body,
  SeverityText AS level,
  ServiceName
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND ('$search' = '' OR positionCaseInsensitive(Body, '$search') > 0)
ORDER BY Timestamp DESC
LIMIT 500
```

### 24. Request outcomes from the audit log (table)

discovery-service logs one `request completed` line per request with the same
ids as the span — useful as a cross-check when a span is missing.

```sql
SELECT
  Timestamp AS time,
  LogAttributes['action'] AS action,
  LogAttributes['status'] AS status,
  toFloat64OrZero(LogAttributes['duration_ms']) AS duration_ms,
  LogAttributes['error_type'] AS error_type,
  LogAttributes['error_code'] AS error_code,
  LogAttributes['transaction_id'] AS transaction_id,
  LogAttributes['request_id'] AS request_id
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
  AND LogAttributes['msg'] = 'request completed'
ORDER BY Timestamp DESC
LIMIT 200
```

### 25. Error log rate by service (table)

```sql
SELECT
  ServiceName AS service,
  countIf(SeverityNumber >= 17) AS errors,
  countIf(SeverityNumber = 13) AS warnings,
  count() AS total
FROM otel.otel_logs
WHERE $__timeFilter(Timestamp)
GROUP BY service
HAVING errors + warnings > 0
ORDER BY errors DESC
```

---

## Metrics

Both instruments are **cumulative monotonic** sums, so a panel wants the
per-interval increase, not the raw value.

### 26. Postgres pool acquire-waits per interval (time series)

`pgxpool.empty_acquire` rising means requests are queueing on the pool before
any SQL is sent — it moves before anything fails, and no other layer can see it.

```sql
SELECT
  time,
  greatest(0, cum - any(cum) OVER (ORDER BY time ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING)) AS empty_acquires
FROM (
  SELECT
    $__timeInterval(TimeUnix) AS time,
    max(Value) AS cum
  FROM otel.otel_metrics_sum
  WHERE $__timeFilter(TimeUnix)
    AND MetricName = 'pgxpool.empty_acquire'
  GROUP BY time
)
ORDER BY time
```

### 27. Time spent waiting on the pool, ms per interval (time series)

```sql
SELECT
  time,
  greatest(0, cum - any(cum) OVER (ORDER BY time ROWS BETWEEN 1 PRECEDING AND 1 PRECEDING)) AS wait_ms
FROM (
  SELECT
    $__timeInterval(TimeUnix) AS time,
    max(Value) AS cum
  FROM otel.otel_metrics_sum
  WHERE $__timeFilter(TimeUnix)
    AND MetricName = 'pgxpool.empty_acquire_wait_time'
  GROUP BY time
)
ORDER BY time
```

### 28. Any metric, generic (time series)

Drop-in for new instruments — add a `metric` variable from
`SELECT DISTINCT MetricName FROM otel.otel_metrics_sum`.

```sql
SELECT
  $__timeInterval(TimeUnix) AS time,
  MetricName AS metric,
  max(Value) AS value
FROM otel.otel_metrics_sum
WHERE $__timeFilter(TimeUnix)
  AND MetricName = '$metric'
GROUP BY time, metric
ORDER BY time
```

---

## Pipeline health

### 29. Is telemetry arriving at all? (stat)

Freshness per signal. A stale number here explains every other panel going
quiet, and distinguishes "no traffic" from "the collector stopped".

```sql
SELECT 'traces' AS signal, max(Timestamp) AS last_seen, count() AS rows_in_range
FROM otel.otel_traces WHERE $__timeFilter(Timestamp)
UNION ALL
SELECT 'logs', max(Timestamp), count()
FROM otel.otel_logs WHERE $__timeFilter(Timestamp)
UNION ALL
SELECT 'metrics', max(TimeUnix), count()
FROM otel.otel_metrics_sum WHERE $__timeFilter(TimeUnix)
```

### 30. Which build is running (table)

```sql
SELECT DISTINCT
  ResourceAttributes['service.name'] AS service,
  ResourceAttributes['service.version'] AS version,
  ResourceAttributes['build.commit'] AS commit,
  ResourceAttributes['build.tree_state'] AS tree_state,
  ResourceAttributes['build.date'] AS built_at,
  ResourceAttributes['network.id'] AS network,
  ResourceAttributes['domain'] AS domain
FROM otel.otel_traces
WHERE $__timeFilter(Timestamp)
```

### 31. ClickHouse storage per table (table)

Ingestion is unsampled and TTL is 720h, so this is the cost panel.

```sql
SELECT
  table,
  sum(rows) AS rows,
  formatReadableSize(sum(bytes_on_disk)) AS on_disk,
  formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed
FROM system.parts
WHERE active AND database = 'otel'
GROUP BY table
ORDER BY sum(bytes_on_disk) DESC
```

---

## Dashboard variables

| Variable | Type | Query |
|---|---|---|
| `service` | Query, multi + All | `SELECT DISTINCT ServiceName FROM otel.otel_logs ORDER BY ServiceName` |
| `action` | Query, multi + All | `SELECT DISTINCT SpanAttributes['beckn.action'] FROM otel.otel_traces WHERE SpanAttributes['beckn.action'] != ''` |
| `schema_type` | Query | `SELECT DISTINCT SpanAttributes['beckn.schemaType'] FROM otel.otel_traces WHERE SpanAttributes['beckn.schemaType'] != ''` |
| `transaction_id` | Textbox | — |
| `trace_id` | Textbox | — |
| `metric` | Query | `SELECT DISTINCT MetricName FROM otel.otel_metrics_sum` |

Wire a multi-value variable into a panel with `$__conditionalAll`, so selecting
*All* does not produce an `IN ('All')` that matches nothing:

```sql
AND $__conditionalAll(SpanAttributes['beckn.action'] IN ($action), $action)
```

---

## All three signals together

Metrics, logs and traces in one panel. Four shapes, in the order you actually
want them.

**Four things to know before adapting these.** Each one is a query that returns
nothing or errors if you get it wrong, and none of them are obvious:

1. **`ORDER BY` binds to the last `SELECT` of a `UNION ALL`, not the union.**
   Wrap the whole union in a subquery and sort outside it, or you get
   `Unknown expression identifier 'time'`.
2. **Metric timestamps are a different type.** `TimeUnix` is `DateTime`;
   `Timestamp` on logs and traces is `DateTime64(9)`. Cast with
   `toDateTime64(TimeUnix, 9)` or the union will not type-check.
3. **A metric is a level, not an event.** The two pool counters emit a point per
   minute whether or not anything happened, so pasted raw into an event stream
   they outnumber the traces they are supposed to give context to. Downsample
   them, as query 33 does.
4. **Logs do not join on `otel_logs.TraceId`.** That column is empty on 11600 of
   11601 rows here, because these logs arrive through the **filelog** receiver
   reading container stdout rather than over OTLP — the collector never fills the
   native column. The id is a field inside the zap JSON that `transform/logs`
   parsed into `LogAttributes['trace_id']`. Join on that.

### 32. Volume of all three signals (time series)

The overview panel: is each signal arriving, and did they stop together (the
collector) or separately (one service)?

```sql
SELECT * FROM (
  SELECT $__timeInterval(Timestamp) AS time, 'traces' AS signal, count() AS events
  FROM otel.otel_traces WHERE $__timeFilter(Timestamp) GROUP BY time
  UNION ALL
  SELECT $__timeInterval(Timestamp), 'logs', count()
  FROM otel.otel_logs WHERE $__timeFilter(Timestamp) GROUP BY 1
  UNION ALL
  SELECT $__timeInterval(TimeUnix), 'metric points', count()
  FROM otel.otel_metrics_sum WHERE $__timeFilter(TimeUnix) GROUP BY 1
)
ORDER BY time
```

Log volume dwarfs the other two, so set the panel's Y axis to **logarithmic**
(Axis → Scale → Logarithmic, base 10) or the trace series flatlines against the
bottom.

### 33. Unified event stream (table)

Traces, logs and metric levels interleaved on one timeline, newest first. This
is the "what was happening at 10:34" panel.

```sql
SELECT * FROM (

  -- Traces: one row per span.
  SELECT
    Timestamp AS time,
    'trace' AS signal,
    ServiceName AS service,
    concat(SpanName, ' -> ', SpanAttributes['http.status_code'],
           ' in ', toString(round(Duration / 1e6, 1)), ' ms') AS detail,
    TraceId AS trace_id
  FROM otel.otel_traces
  WHERE $__timeFilter(Timestamp)

  UNION ALL

  -- Logs: the parsed msg where there is one, the raw body otherwise. Keycloak
  -- and Postgres lines have neither a level nor a msg, hence both fallbacks.
  SELECT
    Timestamp,
    'log',
    ServiceName,
    concat(if(SeverityText = '', '-', SeverityText), ': ',
           if(LogAttributes['msg'] != '', LogAttributes['msg'], substring(Body, 1, 120))),
    LogAttributes['trace_id']
  FROM otel.otel_logs
  WHERE $__timeFilter(Timestamp)
    AND $__conditionalAll(ServiceName IN ($service), $service)

  UNION ALL

  -- Metrics: ONE row per 5-minute bucket carrying every instrument's latest
  -- value, not one row per instrument per scrape. Without the collapse these
  -- are most of the rows in the panel and none of the information.
  SELECT toDateTime64(bucket, 9), 'metric', service,
         arrayStringConcat(groupArray(kv), '   '), ''
  FROM (
    SELECT
      toStartOfInterval(TimeUnix, INTERVAL 300 second) AS bucket,
      ServiceName AS service,
      concat(MetricName, ' = ', toString(argMax(Value, TimeUnix))) AS kv
    FROM otel.otel_metrics_sum
    WHERE $__timeFilter(TimeUnix)
    GROUP BY bucket, service, MetricName
  )
  GROUP BY bucket, service
)
ORDER BY time DESC
LIMIT 500
```

**Set the `$service` variable to `discovery-service` rather than leaving it on
All.** Grafana logs its own query activity to stdout, the collector ships that
back into `otel_logs`, and with everything selected Grafana's lines are 492 of
the 500 rows — the panel ends up mostly showing itself.

Reads like this — the span and the two log lines it produced sit together, and
the metric level is a periodic marker rather than noise:

```
10:34:08.404  log     info: request completed
10:34:08.404  log     warn: rejected request
10:34:08.399  trace   /discover -> 400 in 5.3 ms
10:34:08.386  log     info: request completed
10:34:08.385  trace   discover -> 200 in 1.5 ms
10:35:00.000  metric  pgxpool.empty_acquire = 0   pgxpool.empty_acquire_wait_time = 0
```

### 34. One request — span, events, logs and metrics (table)

Everything the stack recorded about a single request. Drop it on the
troubleshooting dashboard beside the existing panels, or use it standalone with
a `trace_id` textbox variable.

```sql
SELECT * FROM (

  SELECT
    Timestamp AS time,
    '1 span' AS signal,
    concat(SpanName, '  status=', SpanAttributes['http.status_code'],
           '  ', toString(round(Duration / 1e6, 2)), ' ms') AS detail
  FROM otel.otel_traces
  WHERE TraceId = '$trace_id'

  UNION ALL

  -- Span events, flattened. request_info / retrieval_info / response_info /
  -- error -- the intent shape, the providers that answered and the error cause
  -- all live here, not in span attributes.
  SELECT
    ev.1,
    '2 event',
    concat(ev.2, ': ', arrayStringConcat(
      arrayMap(k -> concat(k, '=', ev.3[k]), arraySort(mapKeys(ev.3))), ', '))
  FROM otel.otel_traces
  ARRAY JOIN arrayZip(Events.Timestamp, Events.Name, Events.Attributes) AS ev
  WHERE TraceId = '$trace_id'

  UNION ALL

  SELECT
    Timestamp,
    '3 log',
    concat(SeverityText, ': ', LogAttributes['msg'],
           ' (', LogAttributes['duration_ms'], ' ms)')
  FROM otel.otel_logs
  WHERE LogAttributes['trace_id'] = '$trace_id' OR TraceId = '$trace_id'

  UNION ALL

  -- No metric carries a trace id -- a metric is a level, so the only possible
  -- correlation is temporal. This takes the points within a minute either side
  -- of the span, which answers "was the pool queueing when this request ran?"
  SELECT
    toDateTime64(TimeUnix, 9),
    '4 metric',
    concat(MetricName, ' = ', toString(Value))
  FROM otel.otel_metrics_sum
  WHERE TimeUnix BETWEEN
        (SELECT min(Timestamp) - INTERVAL 60 SECOND FROM otel.otel_traces WHERE TraceId = '$trace_id')
    AND (SELECT max(Timestamp) + INTERVAL 60 SECOND FROM otel.otel_traces WHERE TraceId = '$trace_id')
)
ORDER BY signal, time
```

Output for one discover — the whole request, four signals, twelve lines:

```
10:34:08.385  1 span    discover  status=200  1.46 ms
10:34:08.385  2 event   request_info: intent.filter_type=jsonpath, intent.kinds=["filters","spatial"], …
10:34:08.386  2 event   retrieval_info: retrieval.modes_degraded=[], retrieval.modes_run=["spatial","jsonpath"]
10:34:08.386  2 event   response_info: result.catalog_count=0, result.empty=true, result.provider_ids=[]
10:34:08.386  3 log     info: request completed (1.5 ms)
10:34:21.000  4 metric  pgxpool.empty_acquire = 0
```

The `signal` prefixes are numeric (`1 span`, `2 event`) so one `ORDER BY` sorts
the groups in causal order and each group by time within itself.

### 35. Without a UNION — three queries in one panel

For **time series** panels, the union is optional and usually the wrong tool.
Add three queries to the same panel (**+ Add query** → refIds A, B, C), each
returning `time` and one value column, and Grafana aligns them on the time axis
itself:

```sql
-- A
SELECT $__timeInterval(Timestamp) AS time, count() AS traces
FROM otel.otel_traces WHERE $__timeFilter(Timestamp) GROUP BY time ORDER BY time
```
```sql
-- B
SELECT $__timeInterval(Timestamp) AS time, count() AS logs
FROM otel.otel_logs WHERE $__timeFilter(Timestamp) GROUP BY time ORDER BY time
```
```sql
-- C
SELECT $__timeInterval(TimeUnix) AS time, max(Value) AS pool_waits
FROM otel.otel_metrics_sum
WHERE $__timeFilter(TimeUnix) AND MetricName = 'pgxpool.empty_acquire'
GROUP BY time ORDER BY time
```

Each series keeps its own units and can get its own axis (field override →
*Axis → Placement → Right* on `pool_waits`), which a single unioned column
cannot do. Use the union only when you need the signals in **one table**, where
they must share a schema.
