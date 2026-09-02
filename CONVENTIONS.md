# Chart conventions

Rules every chart in this repository follows. They exist so that a service chart
is predictable to read, safe to upgrade, and traceable back to the change that
produced it.

## Naming

| Thing | Rule | Example |
|---|---|---|
| Chart directory and `name` | `oan-<service>`, kebab-case, matching the service's repo/deployment name | `oan-registry-service` |
| Library chart | `oan-common` — the only library chart; every service chart depends on it | `oan-common` |
| Reference chart | `oan-template` — copied to start a new chart, never deployed as-is | `oan-template` |
| A deployed component | Named after the **role it plays in OAN**, no `oan-` prefix — the prefix is for the shared library and the starter chart | `registry`, `keycloak` |
| Two charts for the same role | Add the distinguishing implementation as a suffix, only when there is something to distinguish | `postgresql-cnpg` |
| Release name | The service name without the `oan-` prefix, so resources read `registry-service-...` not `oan-registry-service-oan-registry-service` | `helm install registry-service charts/oan-registry-service` |
| Template helpers | Chart-local helpers are namespaced by chart name: `<chart>.<helper>` | `oan-registry-service.fullname` |
| Value keys | camelCase, matching Kubernetes field names where one exists | `podSecurityContext`, `envFromSecrets` |
| Env var keys in `envConfig` | SCREAMING_SNAKE_CASE | `LOG_LEVEL` |
| Custom labels/annotations | Prefixed with a domain we own | `oan.in/environment: dev` |

Component charts are named after the role the component plays in OAN, not after
the software that happens to implement it: `registry` and `keycloak`, not
`registry-sunbird-rc` or `keycloak-sunbird-rc`. The implementation is an
implementation detail, and one that can change without the role changing.

Add an implementation suffix only when it actually distinguishes something —
`postgresql-cnpg` carries `-cnpg` because a plain `postgresql` chart could
reasonably mean several different operators, and which one is in use changes how
the chart is configured and operated.

The `oan-` prefix is reserved for the shared library (`oan-common`) and the
starter chart (`oan-template`). Every chart, prefixed or not, depends on
`oan-common` and carries the standard OAN labels, including
`app.kubernetes.io/part-of: oan`.

Chart directory name, `name` in `Chart.yaml`, and the prefix of the chart-local
helpers must all agree. A mismatch is the most common cause of a chart that
lints clean but renders the wrong resource names.

## Versioning

Two independent version fields, both required in every `Chart.yaml`:

- **`version`** — the version of the *chart*, following
  [Semantic Versioning](https://semver.org/). Bumped on every chart change,
  even a comment-only one.
- **`appVersion`** — the version of the *application image* the chart deploys by
  default, quoted. It is the fallback for `image.tag`, so it must be a real,
  pullable tag. Bumping it is a chart change, and so requires a `version` bump
  too.

`version` bump rules:

| Change | Bump |
|---|---|
| New value key with a backward-compatible default; new optional resource | MINOR |
| Bug fix in a template; doc/comment change; `appVersion` bump | PATCH |
| Removing or renaming a value key or helper; changing a default that alters live behaviour; changing an immutable field such as a selector label | MAJOR |

For `oan-common` specifically: consumers pin `version: "0.1.x"`, so helper
additions ship as PATCH/MINOR and MAJOR is reserved for renaming or changing the
behaviour of an existing helper. A MAJOR bump of `oan-common` means every
consuming chart's pin has to be updated deliberately.

Pre-1.0.0 charts are still in flux; once a chart is deployed to production it
goes to `1.0.0` and the rules above are binding.

## Changelog

Every chart keeps a `CHANGELOG.md` in
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format. A chart change
is not complete without both the `version` bump and the matching changelog
entry. This is what makes "which chart version introduced this?" answerable.

## Required in every service chart

The deployment epic requires these on every component, and `oan-common`
enforces the first two at render time rather than leaving them to review:

1. **Resource requests and limits** — `oan-common.resources` fails the render
   when `.Values.resources` is empty. This applies to data stores too, where the
   requests land on the operator-managed pods.
2. **Liveness and readiness probes** — enabled by default in `oan-template`.
   `oan-common.probeSpec` fails the render when an enabled probe declares no
   handler, or declares more than one (which the API server would otherwise
   reject at apply time, long after the render looked fine).
3. **A ServiceAccount per service** — never the namespace `default`. Attach IRSA
   role ARNs via `serviceAccount.annotations`.
4. **Standard labels** — from `oan-common.labels`, giving every resource
   `app.kubernetes.io/*` plus `app.kubernetes.io/part-of: oan`.

## Secrets

No secret value is ever committed to this repository — not in `values.yaml`, not
in a per-environment values file.

- **No chart in this repository renders a Secret.** Charts only *reference*
  Secrets by name, so the question of how secret material gets into the cluster
  is answered once, outside the charts, rather than differently per chart.
- `envFromSecrets` lists names of Secrets whose keys become environment
  variables. `secretEnv` maps one Secret key to one variable name, for when the
  producing key and the expected variable differ.
- `envConfig` is for non-secret configuration only. It lands in a ConfigMap.

## Validation

`./scripts/lint-charts.sh` runs `helm lint --strict` on every chart and
`helm template` on every application chart. CI runs the same script on pull
requests and on pushes to `main` and `development`, so a chart that fails
locally fails the same way in CI.

Because service charts depend on `oan-common` through
`file://../oan-common`, and the packaged dependency is not committed, an edit to
`oan-common` is only visible to a consuming chart after
`helm dependency update charts/<chart>` (or a run of the lint script).

## Traceability

Chart work follows the repository's git conventions: branch
`feat/<issue>-<slug>`, commits carrying `[#<issue>]`, PR body closing the issue.
The chart's changelog entry and the issue number are the two ends of the same
thread.
