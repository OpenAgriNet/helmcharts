# Changelog

All notable changes to the `redis` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-16

### Added
- Initial release: a single Redis pod for decision-support-system form-session
  state (DSS ADR-0022). Deployment (one replica, `Recreate`), `ClusterIP`
  Service on 6379 and ServiceAccount, wired through the `common` library
  chart. Image `redis:7.4-alpine`, the same family DSS's docker-compose runs.
- Runs compose's command - `--requirepass`, `--save ""`, `--appendonly no` -
  plus `maxmemory` (default `100mb`) and `maxmemoryPolicy` (default
  `allkeys-lru`). Nothing is saved to disk and there is no volume: a restart
  comes back empty.
- Password from an existing Secret (`auth.existingSecret`, `auth.passwordKey`);
  the render fails when no Secret is named, so there is no passwordless mode.
- `redis-cli ping` exec probes, matching compose's healthcheck.
- Runs as the image's `redis` user (uid 999, gid 1000 in the alpine image)
  with a read-only root filesystem, all capabilities dropped and no
  service-account token.
