# Changelog

All notable changes to the `asm-secrets` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-18

### Changed
- Each adapter's Secret goes to that adapter's own namespace rather than a
  shared `oan`: `consumer-adapter-keys` to `consumer-adapter`,
  `network-adapter-keys` to `network-adapter`, and both
  `provider-adapter-keys` and `provider-upstream` to `provider-adapter`.

  None of the four is mirrored now, and that is the point. A Secret holding an
  adapter's private signing key, readable from a namespace that adapter does not
  run in, lets anything there sign as that participant. Sharing one namespace
  made all three keys readable to anyone with `get secrets` in it.

  The AWS Secrets Manager entries are unchanged -- same names, same values.
  Only where they are projected moved, so nothing needs re-pushing or rotating.

