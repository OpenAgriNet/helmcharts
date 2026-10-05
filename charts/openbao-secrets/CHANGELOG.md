# Changelog

All notable changes to the `openbao-secrets` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-27

### Added
- One `ExternalSecret` per credential, read from OpenBao through the `openbao`
  store that `openbao-cluster-secret-store` creates, and mirrored by Reflector
  into the namespaces that read it.

  Replaces `asm-secrets`, which reads AWS Secrets Manager: the same secret
  list, the same keys (`oan/<env>/<name>`, under OpenBao's `secret` KV mount)
  and the same namespaces, so every Secret it produces has the name and keys
  the charts already expect.

  Moving a cluster over: copy the values into OpenBao
  (`scripts/asm-to-openbao.sh`), then `helm upgrade` the EXISTING asm-secrets
  release to this chart. The ExternalSecrets keep their names, so they are
  updated in place and no Secret is deleted.

  Do not uninstall `asm-secrets` first. Its ExternalSecrets own their Secrets,
  so uninstalling deletes them -- tested on kind, 17 of 29 were gone until this
  chart was installed -- and any pod that restarts in that window fails.
