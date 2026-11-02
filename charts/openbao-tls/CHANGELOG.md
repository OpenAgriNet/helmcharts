# Changelog

All notable changes to the `openbao-tls` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-27

### Added
- A private CA for OpenBao (`openbao-ca`, 10 years) and the server
  certificate OpenBao serves on 8200 (`openbao-server-tls`, 90 days, renewed
  30 days before expiry with a new key), both issued by cert-manager in
  OpenBao's namespace. The server Secret carries `ca.crt`, which ESO, the bao
  CLI and raft peers trust.
