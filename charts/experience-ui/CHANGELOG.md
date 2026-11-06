# Changelog

All notable changes to the `experience-ui` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-04

### Added
- Initial release, from `template`: Deployment, Service, ServiceAccount,
  optional Ingress.
- `config`: the deployment's `config.json`, as a ConfigMap mounted as a
  directory at `/etc/experience-ui`, which is how the app expects to be
  configured. A checksum annotation rolls the pod when it changes.
- Probes on `/healthz`.
- `appVersion` is a commit of `main`: the app has cut no release, and CI tags
  every image with its commit.
