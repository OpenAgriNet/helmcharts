{{/*
# ============================================================================
# ADAPTER SERVICE CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to common, plus the role,
#          identity, upstream and config-rendering wiring an adapter needs.
#
# One chart, three roles. provider, network and consumer are the same image and the
# same config format; the role decides the handler role, the step list and
# where requests are routed. Install once per role, and keep the RELEASE name
# per-role -- the release name is what becomes the Service DNS name that the
# other adapters address.
# ============================================================================
*/}}

{{/*
The adapter's role: provider | network | consumer.

Required, with no default. A default would silently give one role's step list
and handler role to a different adapter -- which renders, starts, reports Ready
and then mis-handles every request, at the far end, in a peer's logs.
*/}}
{{- define "adapter-service.role" -}}
{{- $role := .Values.role | default "" -}}
{{- if not $role -}}
{{- fail (printf "%s: role is required -- one of provider, network, consumer. It decides the handler role, the step list and the routing target, so there is no safe default. See examples/ for a values file per role." .Chart.Name) -}}
{{- end -}}
{{- if not (has $role (list "provider" "network" "consumer")) -}}
{{- fail (printf "%s: role must be one of provider, network, consumer (got %q)." .Chart.Name $role) -}}
{{- end -}}
{{- $role -}}
{{- end }}

{{/*
The name the adapter calls itself in its config, its logs and its module.
Defaults to <role>-adapter, matching the compose stack's service names.
*/}}
{{- define "adapter-service.appName" -}}
{{- .Values.appName | default (printf "%s-adapter" (include "adapter-service.role" .)) -}}
{{- end }}

{{/*
OTEL service name. Defaults to oan-<role>-adapter, matching OTEL_SERVICE_NAME
in the compose stack so traces from either deployment line up.
*/}}
{{- define "adapter-service.otelServiceName" -}}
{{- .Values.otel.serviceName | default (printf "oan-%s-adapter" (include "adapter-service.role" .)) -}}
{{- end }}

{{/*
bap originates a call, bpp receives one. consumer is the caller; network and
provider receive. Overridable, because the role is a shorthand for a default
rather than a constraint.
*/}}
{{- define "adapter-service.handlerRole" -}}
{{- if .Values.handler.role -}}
{{- .Values.handler.role -}}
{{- else if eq (include "adapter-service.role" .) "consumer" -}}
bap
{{- else -}}
bpp
{{- end -}}
{{- end }}

{{/*
The step list, as a YAML array.

consumer sits inside the trust boundary and accepts unsigned requests, so it has no
signature to validate; the other two receive from the network and must verify
first. Set handler.steps to override.
*/}}
{{- define "adapter-service.steps" -}}
{{- if .Values.handler.steps -}}
{{- toYaml .Values.handler.steps -}}
{{- else if eq (include "adapter-service.role" .) "consumer" -}}
{{- toYaml (list "addRoute" "sign") -}}
{{- else -}}
{{- toYaml (list "validateSign" "addRoute" "sign") -}}
{{- end -}}
{{- end }}

{{/*
The routing config filename. One file per role so a rendered config is
self-describing when you exec into a pod.
*/}}
{{- define "adapter-service.routingFile" -}}
routing-{{ include "adapter-service.role" . }}.yaml
{{- end }}


{{- define "adapter-service.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "adapter-service.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "adapter-service.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "adapter-service.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "adapter-service.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "adapter-service.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{/*
The Secret holding this adapter's keypair.

Failing here rather than letting the render succeed is the point. Without it
the config would carry the literal placeholders through to the process, which
starts, serves /health, reports Ready, and then fails every signature it
attempts -- at the far end, in a peer's logs, with nothing in this pod saying
why.
*/}}
{{- define "adapter-service.keysSecretName" -}}
{{- $name := .Values.keys.existingSecret.name | default "" -}}
{{- if not $name -}}
{{- fail (printf "%s: keys.existingSecret.name is required. This adapter's identity is a keypair, this chart renders no Secrets, and a pod without one starts and reports Ready while failing every signature it makes. See values.yaml for the six keys it must hold." .Chart.Name) -}}
{{- end -}}
{{- $name -}}
{{- end }}

{{/*
The registry API base. Required for the same reason as the keys: absent, the
adapter builds a plugin pointed at nothing and every signature verification
fails on a lookup rather than on the signature.
*/}}
{{- define "adapter-service.registryUrl" -}}
{{- $url := .Values.registry.url | default "" -}}
{{- if not $url -}}
{{- fail (printf "%s: registry.url is required -- e.g. http://registry:8081/api/v1. The adapter verifies every caller against the key the registry publishes for them, so with no registry it can verify nobody." .Chart.Name) -}}
{{- end -}}
{{- $url -}}
{{- end }}

{{/*
OTLP endpoint, required only when telemetry is on. An enabled exporter with
no endpoint dials nothing and logs a failure per export interval, which is
the noise the three enable flags exist to avoid.
*/}}
{{- define "adapter-service.otlpEndpoint" -}}
{{- if .Values.otel.enabled -}}
{{- $ep := .Values.otel.endpoint | default "" -}}
{{- if not $ep -}}
{{- fail (printf "%s: otel.endpoint is required when otel.enabled is true -- e.g. otel-collector.observability.svc.cluster.local:4317. An enabled exporter with nowhere to send logs a failure every export interval." .Chart.Name) -}}
{{- end -}}
{{- $ep -}}
{{- end -}}
{{- end }}

{{/*
Where the rendered config lands. An emptyDir, because the init container
writes it and readOnlyRootFilesystem means there is nowhere else to write.
*/}}
{{- define "adapter-service.configDir" -}}
/app/config
{{- end }}

{{/*
Annotation that rolls the pods when the config changes.

The keys are not in this checksum and must not be: the Secret is not rendered
by this chart, so its content is not knowable at template time. Rotating a key
therefore needs a rollout restart -- which the NOTES print.
*/}}
{{- define "adapter-service.configChecksum" -}}
{{- include (print $.Template.BasePath "/configmap.yaml") . | sha256sum -}}
{{- end }}

{{/*
The catalog crawler's two application-level plugin blocks, indented to sit under
the config's top-level `plugins:` key -- beside otelsetup, which is the same kind
of thing: started once at boot, not per request.

Validated here rather than defaulted: the participant and receiver ids decide who
the catalogs are published as and to, and a guess publishes under someone else's
name.
*/}}
{{- define "adapter-service.crawlerBlock" -}}
{{- $c := .Values.crawler -}}
{{- if ne (include "adapter-service.role" .) "network" -}}
{{- fail (printf "%s: crawler.enabled is only valid for the network role (this is %q). The crawler publishes THROUGH the provider adapter and this adapter is where the registry sweep runs." .Chart.Name (include "adapter-service.role" .)) -}}
{{- end -}}
{{- if or (not $c.participantId) (not $c.receiverId) -}}
{{- fail (printf "%s: crawler.participantId and crawler.receiverId are required when crawler.enabled is true. They are stamped on every publish as context.senderId and context.receiverId, and have no default because a guessed identity publishes under someone else's name." .Chart.Name) -}}
{{- end -}}
{{- if not $c.dbDsnKey -}}
{{- fail (printf "%s: crawler.dbDsnKey is required when crawler.enabled is true: the run log's database DSN comes from that key in the keys Secret." .Chart.Name) -}}
{{- end -}}
{{- if not (hasPrefix "/" (include "adapter-service.crawlerOutputDir" .)) -}}
{{- fail (printf "%s: crawler.publishCatalogOutputDir must be an absolute path (got %q)." .Chart.Name (include "adapter-service.crawlerOutputDir" .)) -}}
{{- end -}}
registry:
  id: sunbirdRegistry
  config:
    url: {{ $c.registryUrl | quote }}
    entity: Participant
    providerEntity: ProviderSchema

crawler:
  id: catalogcrawler
  config:
    dbDsn: "__ADAPTER_CRAWLER_DB_DSN__"
    discoveryPushUrl: {{ $c.discoveryPushUrl | quote }}
    participantId: {{ $c.participantId | quote }}
    receiverId: {{ $c.receiverId | quote }}
    publishPipelines: {{ $c.publishPipelines | toString | quote }}
    publishEnabled: {{ $c.publishEnabled | toString | quote }}
    catalogIntervalSeconds: {{ $c.catalogIntervalSeconds | toString | quote }}
    publishCatalogOutputDir: {{ include "adapter-service.crawlerOutputDir" . | quote }}
{{- range $name, $overrides := $c.pipelines }}
{{- if not (regexMatch "^[a-z0-9][a-z0-9-]*$" $name) }}
{{- fail (printf "%s: crawler.pipelines key %q must be the pipeline's metadata.name: lowercase letters, digits and hyphens." $.Chart.Name $name) }}
{{- end }}
{{- range $key, $value := $overrides }}
{{- if not (regexMatch "^[A-Za-z][A-Za-z0-9]*$" $key) }}
{{- fail (printf "%s: crawler.pipelines.%s has the key %q; it must be the name of the pipeline's schedule or one of its inputs." $.Chart.Name $name $key) }}
{{- end }}
    {{ printf "publish.%s.%s" $name $key }}: {{ $value | toString | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Where the crawler keeps the catalogs it builds. /tmp/catalogs unless
crawler.publishCatalogOutputDir says otherwise: the chart mounts an emptyDir
there, because the root filesystem is read-only.
*/}}
{{- define "adapter-service.crawlerOutputDir" -}}
{{- .Values.crawler.publishCatalogOutputDir | default "/tmp/catalogs" -}}
{{- end }}
