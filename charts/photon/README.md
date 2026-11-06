# photon

[Photon](https://github.com/komoot/photon), a place search built on
OpenStreetMap. The DSS asks it for place names its bundled area file does not
carry (`DSS_PHOTON_BASE_URL`, `photon.baseUrl` in the `decision-support-system`
chart). Optional for the DSS: a Photon that is down or slow counts as "no
answer", and the turn still finishes.

## What it renders

| Resource | Notes |
|---|---|
| Deployment | One replica, `Recreate`. An `index` init container fills the volume; the `photon` container serves on 2322 |
| PersistentVolumeClaim | `<release>-data`, holds the jar and the extracted index. Skipped with `persistence.existingClaim` |
| Service | `ClusterIP` on 2322 |
| ConfigMap (env) | From `envConfig` |
| ServiceAccount | `automountServiceAccountToken: false` |

## Install

```bash
helm dependency update charts/photon
helm install photon charts/photon -n dss
```

The first start downloads the index (India: about 600 MB compressed), so the
pod stays in `Init` for several minutes. Later starts skip the download and
take about a minute.

```bash
kubectl -n dss logs deploy/photon -c index -f
kubectl -n dss port-forward svc/photon 2322
curl 'http://localhost:2322/api?q=Pune&limit=1'
```

## Configuration

| Value | Default | Notes |
|---|---|---|
| `photon.version` | `1.3.0` | The Photon release; the jar comes from its GitHub release unless `photon.jarUrl` is set. Photon 1.x reads only the `1.0` index format |
| `photon.indexUrl` | India, from GraphHopper's mirror | Any `photon-db-*-1.0-latest.tar.bz2`. Changing it re-downloads on the next start |
| `photon.extraArgs` | `[]` | Extra `serve` arguments, e.g. `-max-results`, `-default-language` |
| `javaOpts` | `-XX:MaxRAMPercentage=75` | `JAVA_TOOL_OPTIONS`; sizes the heap from the memory limit |
| `persistence.size` | `10Gi` | India extracts to a few GB. A continent or the planet needs far more; measure first |
| `resources` | 250m / 1Gi request, 1 / 2Gi limit | Measure under real lookups |
| `image.*` | `eclipse-temurin:21-jre` | The JRE the jar runs on; `appVersion` is this tag |
| `index.image.*` | `alpine:3.20` | The download step |

The download needs egress to `github.com` (the jar) and
`download1.graphhopper.com` (the index).

## How the download decides

The `index` init container keeps a `.index-ready` marker holding the URL of
the index that finished downloading:

- the jar is fetched only when it is not already in the volume;
- the index is fetched when the marker is missing (a first start, or a
  download cut short) or names a different URL;
- a failed download exits non-zero and writes no marker, so Kubernetes
  retries the init container and the next attempt starts clean.

## Not covered

- **Updates.** The index is a snapshot. To take a newer one, delete
  `.index-ready` from the volume (or change `photon.indexUrl`) and restart.
- **Production use.** The DSS's own compose file calls Photon "for a local
  try, not for production". Agree a geocoder for production before relying on
  this one there.
