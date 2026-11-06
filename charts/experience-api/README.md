# experience-api

The [Experience API](https://github.com/OpenAgriNet/experience-api): takes one
chat turn on `POST /v1/chat`, calls the DSS's `POST /v1/turns`, and streams the
answer back as server-sent events.

## Values

| Value | Env | Notes |
|---|---|---|
| `senderId` | `EXPERIENCE_API_SENDER_ID` | Required. The network participant this deployment serves |
| `dssBaseUrl` | `EXPERIENCE_API_DSS_BASE_URL` | Required. e.g. `http://decision-support-system.dss.svc.cluster.local:8077` |
| `channel` | `EXPERIENCE_API_CHANNEL` | Default `web` |
| `maxCharacters` | `EXPERIENCE_API_MAX_CHARACTERS` | Default `1200` |

The render fails without `senderId` or `dssBaseUrl`.

## Behind a proxy

The app serves `/v1/chat` and `/healthz` at its root, so a proxy mounting it
under a path strips the prefix. A turn streams for up to 120 seconds, so the
proxy must not buffer the response and must wait that long. On Kong:

```yaml
ingress:
  annotations:
    konghq.com/strip-path: "true"
    konghq.com/response-buffering: "false"
service:
  annotations:
    konghq.com/read-timeout: "120000"
```

## Image

`ghcr.io/openagrinet/experience-api`, public, linux/amd64 only. No release has
been cut; CI tags every image with its commit, so `appVersion` is a commit of
`main`. Runs as uid 999 with a read-only root filesystem.
