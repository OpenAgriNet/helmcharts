# Changelog

All notable changes to the `cert-manager-issuers` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-20

### Added
- Let's Encrypt `ClusterIssuer`s, staging and production, solving HTTP-01
  through an ingress class.

  Separate from the `cert-manager` chart because a `ClusterIssuer` is an
  instance of a CRD that chart registers, and one release cannot both define a
  CRD and create an object of that kind.

  Both issuers are enabled by default. Staging exists to be used first: its
  rate limits are loose, while production allows only five failed
  authorizations per hostname per hour, which is easy to exhaust while DNS or
  port 80 is still being sorted out.
