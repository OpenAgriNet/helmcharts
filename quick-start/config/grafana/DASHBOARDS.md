# Observability dashboards

Three dashboards, provisioned from `provisioning/dashboards/json/` into the
**OAN** folder on boot. All three read only ClickHouse, which is to say only
what the OTel Collector received. `QUERIES.md` and `EXPLORE.md` hold ad-hoc SQL
for anything they don't answer.

| Dashboard | Answers | Reads |
|---|---|---|
| **Infra — Resource Usage** (`infra-overview`) | How much CPU, memory, disk and network are the services using? | ClickHouse metrics |
| **Network API — Requests, Errors and Performance** (`network-api`) | For every API: how many requests, how many failed and why, and how fast? | ClickHouse traces and logs |
| **Providers — Publishing and Discovery** (`providers`) | Which providers publish their catalogues, and whose data comes back in discover results? | ClickHouse logs and traces |

Open them at `http://127.0.0.1:${GRAFANA_UI_PORT:-8085}/d/<uid>`.

On Kubernetes the same dashboards ship as the [`dashboards`](../../../charts/dashboards)
chart; its README covers the Grafana and collector values they need, and the
Infra queries that differ there.

---

## Infra

**Service** filter at the top; every panel has one line per service.

| Panel | Metric | Notes |
|---|---|---|
| CPU usage | `container.cpu.utilization` | 100% is one core |
| Memory usage | `container.memory.usage.total` | Excludes page cache |
| Disk I/O | `container.blockio.io_service_bytes_recursive` | Bytes read / written per second |
| Database size | `postgresql.db_size` | Disk space the two databases take |
| Network I/O | `container.network.io.usage.{rx,tx}_bytes` | Bytes in / out per second. In the collapsed row at the bottom |
| Database connections | `postgresql.backends` vs. `postgresql.connection.max` | New requests wait when they meet. In the collapsed row at the bottom |

**Disk is activity plus database size, not space per service.** The Docker
stats API reports no per-container disk usage. The databases are where the
stack's data lives, so their size is the space that grows. Cumulative counters
(disk, network) are diffed per device or interface, and negative steps (a
restart) are dropped.

**Not collected:** service restarts.

## Network API

**Filters:** API (publish, discover, and any other the services receive),
Service, and Period (last 24 hours, 7 days or 30 days, for the Overview table).

Written to be read by someone who is not an engineer: plain words, standard
names, no status codes on the main view. One colour per meaning everywhere:
blue for all requests, green for successful, red for failed and error rate.

| Section | What it shows |
|---|---|
| **Overview** | One table row: total requests, successful, failed and error rate for the **Period** picked at the top: last 24 hours, 7 days or 30 days. API and Service apply; the time range picker does not |
| **Requests** | One chart: total, successful and failed requests per minute, all three as lines |
| **Performance** | A one-line explainer; tiles for *Within target time* (share of requests within target: 0.5 s discover, 2 s publish, 1 s anything else; green from 99%, yellow from 95%) and **p90**, **p95**, **p99**; and one chart with those three lines (blue, orange, purple). p90 = 90% were faster, p95 = 95%, p99 = 99% |
| **APIs** | *Requests by API*: requests, successful, failed, error rate, p90 / p95 / p99, and *Performance* (OK / Slow / Very slow, from p95 against the target) |
| **Errors** | *Top errors*: the most common error codes and messages, with how many requests each affected |

Every chart draws about 60 buckets across the time range (`maxDataPoints`),
so bars stay wide on a long range.

### How requests are counted

A request passes through several services, and every one of them records it:

| API | Path |
|---|---|
| discover | consumer-adapter → network-adapter → discovery-service |
| publish | provider-adapter → network-adapter → discovery-service |

So adding the records up triples every number. The dashboard keys one request
by its `message_id`, which is the same on every service it passes through:

- **Service = All** counts each request once, at the **entry point**: its
  earliest record, the service the caller talked to. That is what the caller
  experienced.
- **A specific service** counts only that service's records: its own view.

Nothing is hard-coded per service, so a new service or API appears on its own.

### What the services record, and how the dashboard reads it

| Fact | Where it comes from | Why |
|---|---|---|
| API | span name, without the leading `/` | The adapters name it `/discover`, discovery `discover` |
| Status | `http.response.status_code`, else `http.status_code` | The adapters and discovery use different attribute names |
| Failed | status ≥ 400, or span status `Error` | Discovery leaves a rejected request's span status `Unset` |
| Error code and message | the adapters' audit `response` log (the NACK body), or discovery's log line carrying `error_code` | Spans carry only `status code is invalid` |
| Providers in a publish, and whether each catalogue was accepted | the adapters' audit `request` log (`catalogs[].provider`) and `response` log (`results[].status`, `stats.itemCount`) | The publish span doesn't name them |
| Providers in a discover answer | the adapters' audit `response` log, `catalogs[].provider.id` | |
| Empty discover | `result.empty` on discovery's span | Only discovery knows |

**Slow** starts at p95 over 500 ms for discover, 2 s for publish and 1 s for
anything else (the Performance column in *Requests by API*). Publish does more work before it
forwards, so one level for both would be wrong for one of them.

## Providers

Everything here comes from what the adapters log for each request. Each
request is matched to its API through the `message_id` on its span.

A **catalogue** is the list of data a provider offers; **publishing** sends it
to the network so discover can find it. Counts are of catalogues, not requests.

**Filters:** Provider, and Stale after. A provider is shown by its short name:
*Knowledge* (`bharat-vistaar`) and *PoCRA* (`pocra`) are set in the dashboard
queries (`transform(...)`), and any other provider shows its catalogue's
`provider.descriptor.name`, or its id when that is empty. Change the names in
the provider catalogues, and the mapping can go.

| Panel | Means |
|---|---|
| Published providers | At least one catalogue published in the time range |
| Providers in discover results | Its data came back in at least one discover |
| Stale providers | Last catalogue published is older than **Stale after** (a filter at the top, 7 days by default) |
| Catalogues published, Catalogues rejected | Rejected means the request was refused, or discovery did not accept the catalogue |
| Provider status | Status, last published, catalogues published, catalogues rejected, items in the last publish, in discover results |
| Catalogues published, In discover results (charts) | The same over time, one colour per provider |

The time range defaults to **30 days**, the telemetry's retention.

**Registered providers are not visible yet.** The registry (Sunbird RC
v2.0.0) emits no OpenTelemetry:
- there is no OTel SDK and no metrics endpoint in the image;
- its entity events go to Kafka, and are switched off (`event_enabled=false`).

So a provider that registered but never publishes, or whose status changed in
the registry, does not show here. It appears once it publishes or answers a
discover within the time range. Covering registrations means making the
registry send telemetry to the collector, which is later work.

---

## Changing a dashboard

The JSON files are the source. `allowUiUpdates: false`, so an edit made in the
browser is overwritten on the next 30-second scan. Iterate in the UI, then copy
**Dashboard settings → JSON Model** back into the file, keeping `uid` as it is
and `id` null.

Every panel refers to the datasource by its pinned uid, `clickhouse`. Read the
`deleteDatasources` note in `clickhouse.yaml` before changing it.

## Checking the dashboards

1. **Send traffic:**
   - a few correct publish and discover requests;
   - a few bad ones: a wrong field name, a broken filter expression.
2. **Infra:** every service appears; the lines rise under load and settle.
3. **Network API**, over a short time range such as the last 15 minutes:
   - *Total requests* equals the number of calls sent, not three times that.
   - A wrong field name and a broken filter both appear in *Top errors* with
     the error code and message the caller got.
4. **Providers:**
   - publish from one provider, and its *Last published* and *Catalogues published* move;
   - run a discover, and *In discover results* goes up for each provider in
     the answer.
