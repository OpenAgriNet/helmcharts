Mounted over `provisioning/alerting` when `GRAFANA_ALERTING_ENABLED=false`
(see the `grafana` service in docker-compose.yml). Deliberately empty of rules
and contact points: Grafana with alerting off refuses to start if it is given
any, so dashboards would go down with it. Grafana only reads .yaml/.yml files
here, so this README is ignored.
