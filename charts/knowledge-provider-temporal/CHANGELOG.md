# Changelog

All notable changes to the `knowledge-provider-temporal` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment, Service, ServiceAccount and env ConfigMap,
  wired through the `common` library chart. No Ingress - internal-only.
- `exec` startup/liveness/readiness probes running `tctl cluster health`,
  matching `docker-compose.yml`'s healthcheck.
- `database.*` structured values (`name`, `visibilityName`, `host`, `port`,
  `user`, `passwordSecret`) deriving the `temporalio/auto-setup` image's own
  env var contract (`DB`, `DB_PORT`, `POSTGRES_SEEDS`, `POSTGRES_USER`,
  `POSTGRES_PWD`, `DBNAME`, `VISIBILITY_DBNAME`), with `waitFor` blocking on
  the database before starting.
- README documents the two-database requirement (`temporal` +
  `temporal_visibility`).

### Fixed
- README corrected after installing against a real cluster: CNPG's
  `databases:` list cannot express `temporal_visibility` (underscore in the
  name breaks the generated K8s object name), and `temporalio/auto-setup`
  needs `CREATEDB` on the connecting role regardless, since it always
  attempts to create the visibility database itself rather than checking
  first. Corrected guidance grants `CREATEDB` via `postInitApplicationSQL`
  and lets the entrypoint create it.
- Probe `exec` commands run `tctl --address $POD_IP:7233 cluster health`
  through a shell, with `POD_IP` populated via the Downward API - neither
  `localhost` nor `127.0.0.1` ever works here. Two real findings from
  installing against a live cluster: `tctl` first resolved `localhost` to
  the IPv6 loopback while the server listens on IPv4 only, and after fixing
  that to `127.0.0.1`, the probe *still* failed - `/proc/net/tcp` showed the
  frontend gRPC listener bound only to the pod's own routable IP (the
  `--env docker` preset's behavior), never to loopback or `0.0.0.0`, even
  though the server was fully healthy and serving the whole time. The
  Service was never affected by this - only exec probes running inside the
  pod were.
- Deployment `strategy` is `Recreate`, not the default `RollingUpdate`.
  `temporalio/auto-setup` registers in a Postgres-backed cluster membership
  ring even running as a single process; a rolling update runs old and new
  pods together, and each tries to route to the other as if it were a
  cluster peer - producing repeated dial-timeout loops that stalled the new
  pod's readiness for many minutes in testing. `Recreate` guarantees only
  one member ever exists.
