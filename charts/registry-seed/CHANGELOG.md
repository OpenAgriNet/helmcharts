# Changelog

All notable changes to the `registry-seed` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-09-20

### Fixed
- The placeholder guard matches angle brackets ANYWHERE in a value, not only as
  a whole one.

  It tested `hasPrefix "<"` and `hasSuffix ">"`, which is exact for a value
  written out whole -- `<consumer.oan.example.com>` -- and blind to one that is
  composed. `baseUrl: https://consumer.{{ .Values.global.domain }}` with an
  unfilled domain renders `https://consumer.<IP>.sslip.io`: it neither starts
  with `<` nor ends with `>`, so the old test passed it straight through.

  That is precisely the mistake the guard exists to stop. A seeded `baseUrl` is
  permanent -- the registry cannot update a record and its delete is soft -- so
  an unfilled address becomes an adapter identity that can only be abandoned
  under a new participant id, never corrected.

## [0.1.0] - 2026-09-15

### Added
- Initial chart. Seeds the registry with adapter identities, upstream
  providers, capability bindings and capability schemas, then reports the key
  osid each adapter must be configured with.

  A port of `quick-start/bin/setup.py`'s `seed()` and `key_osids()`. Two
  properties are carried over deliberately, because the registry is append-only
  with a soft delete that keeps the unique index:

  - every write checks first and leaves what it finds alone, so re-running is
    safe;
  - the read-back verifies the registry's published public key still matches
    the one being configured, and fails naming the participant rather than
    letting a mismatch surface later as an unexplained authentication error.

  Missing fields that cannot be corrected after a write — `participantId`,
  `baseUrl`, `signingPublicKey`, `path`, `mappingUrl` — fail the render rather
  than being defaulted.

- `examples/seed.dev.yaml` — the seven participants, four bindings and four
  capability schemas the compose stack seeds, so the cluster deployment is a
  filled-in copy of a known-good one rather than a fresh guess.

- Placeholder refusal, ported from `setup.py`'s `is_placeholder()`. A value
  still in its `<describe-it-here>` form fails the render, and every remaining
  placeholder is reported in one list rather than one per attempt. Angle
  brackets are an exact test: no real id, URL or path can be written that way.

- The script is stdlib-only, so it runs on a plain `python:3.13-slim` with
  nothing installed, as non-root with a read-only root filesystem, its own
  ServiceAccount and no API token mounted.
