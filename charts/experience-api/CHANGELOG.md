# Changelog

All notable changes to the `experience-api` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-04

### Added
- Initial release, from `template`: Deployment, Service, ServiceAccount, env
  ConfigMap, optional Ingress.
- `senderId`, `dssBaseUrl`, `channel` and `maxCharacters` as values; the render
  fails without the first two, which the app needs to start or to answer.
- Probes on `/healthz`. Runs as the image's own non-root user (999) with a
  read-only root filesystem and a writable `/tmp`.
- `appVersion` is a commit of `main`: the app has cut no release, and CI tags
  every image with its commit.
