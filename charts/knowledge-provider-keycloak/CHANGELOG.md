# Changelog

All notable changes to the `knowledge-provider-keycloak` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-21

### Added
- Initial release: Deployment (`start --import-realm`), Service, ServiceAccount,
  env ConfigMap, optional Ingress and optional PersistentVolumeClaim, all
  wired through the `common` library chart.
- Two container ports (`http` 8080, `health` 9000) - modern Keycloak exposes
  health endpoints on a separate Quarkus management interface, not the main
  HTTP port. Startup/liveness/readiness probes target `health`.
- `database.*` structured values assembling `KC_DB_URL`, with `waitFor`
  blocking on the database before Keycloak starts.
- `admin.*` and `smtp.*` structured values for bootstrap admin credentials and
  the email-OTP 2FA plugin's SMTP settings.
- Deliberately does **not** mount a realm-import ConfigMap or a provider-jar
  volume: this chart's image already bakes both in at build time.

### Fixed
- `start --import-realm` is now rendered as `args`, not `command`. Setting it
  as `command` replaces the image's ENTRYPOINT (`/opt/keycloak/bin/kc.sh`)
  entirely, so the container tried to exec a literal binary named `start` and
  crashlooped with `exec: "start": executable file not found in $PATH` -
  found by installing this chart against a real cluster.
- Probe paths are `/auth/health/{live,ready,started}`, not bare
  `/health/*`. `KC_HTTP_RELATIVE_PATH` turns out to prefix the Quarkus
  management interface's health endpoints too, not just the main app - the
  bare paths 404'd against a real deployment.
