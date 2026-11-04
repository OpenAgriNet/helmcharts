# experience-ui

The [Experience UI](https://github.com/OpenAgriNet/experience-ui): the
reference web chat client, a static bundle served by nginx on 8080.

## Where it can be served

The base path is baked into the image at build time. The published image is
built for **`/experience/`** (the repository's `VITE_BASE_PATH` variable), so:

- a proxy in front must strip `/experience` before the request reaches this pod;
- the API is reached at `api.baseUrl` from `config.json`, `/experience/api` by
  default, on the same origin, so the proxy routes that path to the
  Experience API.

On Kong, `konghq.com/strip-path: "true"` on a `Prefix` path `/experience` does
the first; the bare `/experience` then lands on the app's root too.

## Configuration

`config` is the deployment's whole `config.json`: name, logo, colours, feature
switches, and `api.baseUrl`. Empty, the image's own copy is used. Set, it
**replaces** that copy rather than merging with it, so start from
`src/config/app-config.json` in the app repo.

It is mounted as a directory at `/etc/experience-ui`, not a single file: a
`subPath` mount never sees ConfigMap updates. nginx serves it with
`Cache-Control: no-store`, and a checksum annotation rolls the pod when it
changes.

## Image

`ghcr.io/openagrinet/experience-ui`, public. No release has been cut; CI tags
every image with its commit, so `appVersion` is a commit of `main`. Security
contexts are off: nginx's master process runs as root in this image.
