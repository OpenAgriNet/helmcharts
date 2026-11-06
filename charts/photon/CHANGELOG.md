# Changelog

All notable changes to the `photon` chart are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-04

### Added
- Initial release: Photon (komoot/photon) as the DSS's place search,
  following the `photon-index` and `photon` services in
  `decision-support-system/docker-compose.yml`.
- An `index` init container that downloads the Photon jar and a regional
  place index into a PersistentVolumeClaim once, and skips the download on a
  restart. A marker file holds the URL of the index that finished
  downloading, so a changed `photon.indexUrl` or a download cut short fetches
  it again. Runs on `alpine`, whose BusyBox has wget, bunzip2 and tar, where
  compose apt-get installs bzip2 into the JRE image on every first start.
- Defaults to Photon `1.3.0` and the India index, as the DSS's compose file.
- `Recreate` strategy and a render failure above one replica, since every
  replica would mount the same ReadWriteOnce volume.
- Startup, liveness and readiness probes on `/api?q=a&limit=1`, which any
  index answers with 200.
