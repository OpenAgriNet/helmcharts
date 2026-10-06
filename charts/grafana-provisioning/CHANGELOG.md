# Changelog

All notable changes to the `grafana-provisioning` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-29

### Added
- Dashboard (#49): `oan-discovery-overview`, as a ConfigMap for the grafana
  chart's dashboards sidecar, in the `OAN` folder.
- Functional alert rules (#34), in the `functional-alerts` folder:
  - **API failure rate**: 5xx rate above 5% over 5 minutes, per service. Critical.
  - **High latency**: discover p95 above 2 seconds for 10 minutes. Warning.
  - **Service down**: no log line, span or metric from a service for 5 minutes.
    Critical. Metrics are what keep an idle service, with no traffic, from
    reading as down.

  The failure rate and latency need at least 20 requests in the window before
  they can fire.
- Slack notifications (#34): a `slack` contact point posting a short message
  per alert (a one-line summary, a one-line description and a link to the
  dashboard), and a policy routing every alert to
  it, grouped by alert and service. Each incident notifies once when it fires
  and once when it resolves; a still-firing alert is not repeated for 5 days. The webhook URL is read from
  `SLACK_WEBHOOK_URL` on the Grafana container.

Every file is byte-identical to its quick-start copy, and
`scripts/lint-charts.sh` fails if they drift. Everything queries the ClickHouse
datasource (uid `clickhouse`).
