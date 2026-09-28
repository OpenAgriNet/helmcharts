# Changelog

All notable changes to the `openbao-cluster-secret-store` chart are documented
here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-25

### Added
- A `ClusterSecretStore` named `openbao`, reading the KV v2 mount `secret` on
  the in-cluster OpenBao and logging in with Kubernetes auth as the
  `external-secrets` ServiceAccount.

  Replaces `asm-cluster-secret-store`, which points at AWS Secrets Manager
  through IRSA. `openbao-secrets` reads the same keys through it,
  `oan/<env>/<name>`, that `asm-secrets` read through that one.
- `allowedNamespaces` (default `[external-secrets]`), rendered as the store's
  `spec.conditions`. ESO logs in to OpenBao as itself whichever namespace an
  ExternalSecret is in, so without it an ExternalSecret in any namespace could
  read every credential. Required: the chart fails to render with it empty.
