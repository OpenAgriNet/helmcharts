{{/*
# ============================================================================
# DISCOVERY SERVICE CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to oan-common, plus the database,
#          Beckn specification and environment wiring this service needs.
# ============================================================================
*/}}

{{- define "discovery.name" -}}
{{- include "oan-common.name" . -}}
{{- end }}

{{- define "discovery.fullname" -}}
{{- include "oan-common.fullname" . -}}
{{- end }}

{{- define "discovery.labels" -}}
{{- include "oan-common.labels" . -}}
{{- end }}

{{- define "discovery.selectorLabels" -}}
{{- include "oan-common.selectorLabels" . -}}
{{- end }}

{{- define "discovery.serviceAccountName" -}}
{{- include "oan-common.serviceAccount.name" . -}}
{{- end }}

{{- define "discovery.image" -}}
{{- include "oan-common.image" . -}}
{{- end }}

{{- define "discovery.envConfigMapName" -}}
{{- include "oan-common.envConfigMapName" . -}}
{{- end }}

{{/*
Absolute path of the Beckn spec cache, VALIDATION_SPEC_CACHE_PATH.

Always absolute and always set explicitly, rather than left to the service's
own default of ".cache/beckn/beckn.yaml": that one is relative to the working
directory, which makes the path a fact about the image rather than about this
chart, and the volume mount below has to name a directory that agrees with it.
*/}}
{{- define "discovery.specCacheDir" -}}
{{- .Values.becknSpec.cacheDir | trimSuffix "/" -}}
{{- end }}

{{- define "discovery.specCachePath" -}}
{{- printf "%s/%s" (include "discovery.specCacheDir" .) .Values.becknSpec.key -}}
{{- end }}

{{/*
Emits "true" when the spec cache is backed by a ConfigMap rather than by an
emptyDir the fetched document is written into.
*/}}
{{- define "discovery.specFromConfigMap" -}}
{{- if .Values.becknSpec.existingConfigMap -}}
{{- true -}}
{{- end -}}
{{- end }}

{{/*
The DSN, when this chart assembles one. Empty when database.urlSecret is set,
because then the whole DSN comes from that Secret instead.

The password is NOT interpolated here - it is referenced as $(DATABASE_PASSWORD)
and expanded by the kubelet against the secretKeyRef env var defined just above
it in the container spec. That keeps the password out of the rendered manifest,
out of `helm get values`, and out of anything that logs a template.

Because that expansion is textual, a password containing a URL delimiter
(@ : / ? # %) must already be percent-encoded in the Secret. urlSecret avoids
the question entirely, which is why it is the documented default.
*/}}
{{- define "discovery.databaseURL" -}}
{{- $db := .Values.database -}}
{{- printf "postgres://%s:$(DATABASE_PASSWORD)@%s:%v/%s?sslmode=%s" $db.user $db.host $db.port $db.name $db.sslMode -}}
{{- end }}

{{/*
Everything the service needs that this chart derives from its structured
values, as container env entries. These take precedence over the envConfig
ConfigMap injected with envFrom, which is what makes "derived here, overridable
there" safe rather than ambiguous.

Every guardrail below refuses to render something the service would refuse to
boot on. The failure is the same either way; the difference is that this one
names the value and happens before anything is applied to the cluster.
*/}}
{{- define "discovery.env" -}}
{{- $db := .Values.database -}}
{{- $spec := .Values.becknSpec -}}
{{- if not .Values.app.networkId }}
{{- fail (printf "%s: app.networkId is required - it is APP_NETWORK_ID, which the boot refuses without, and it decides who a catalog published with no explicit visibleTo is visible to. Set the network this deployment serves, e.g. mahavistar." .Chart.Name) }}
{{- end }}
{{- if and (not $db.urlSecret.name) (not $db.passwordSecret.name) }}
{{- fail (printf "%s: the database DSN is unset. Set database.urlSecret.name (preferred - CNPG writes a ready-made `uri` key into Secret/<cluster>-app), or database.passwordSecret.name together with database.host/name/user to have the chart assemble one. This chart renders no Secrets." .Chart.Name) }}
{{- end }}
{{- if and (not $db.urlSecret.name) (not $db.host) }}
{{- fail (printf "%s: database.host is required when the DSN is assembled - point it at the PostgreSQL primary service, e.g. discovery-db-rw" .Chart.Name) }}
{{- end }}
{{- if and (not $spec.url) (not $spec.existingConfigMap) }}
{{- fail (printf "%s: set becknSpec.url or becknSpec.existingConfigMap. The service loads the Beckn document before it serves and refuses to start without it, and with neither set there is nothing to fetch and nothing cached to fall back to." .Chart.Name) }}
{{- end }}
{{- if not $spec.key }}
{{- fail (printf "%s: becknSpec.key is required - it is both the key read from the ConfigMap and the filename the cache is written under" .Chart.Name) }}
{{- end }}
{{- if $db.urlSecret.name -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $db.urlSecret.name }}
      key: {{ $db.urlSecret.key }}
{{- else }}
{{- /* Defined FIRST: the kubelet expands $(VAR) only against variables that
       precede it in this list. */ -}}
- name: DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $db.passwordSecret.name }}
      key: {{ $db.passwordSecret.key }}
- name: DATABASE_URL
  value: {{ include "discovery.databaseURL" . | quote }}
{{- end }}
- name: DATABASE_AUTO_MIGRATE
  value: {{ $db.autoMigrate | quote }}
- name: DATABASE_MAX_CONNS
  value: {{ $db.maxConns | quote }}
- name: DATABASE_MIN_CONNS
  value: {{ $db.minConns | quote }}
- name: APP_NETWORK_ID
  value: {{ .Values.app.networkId | quote }}
- name: APP_DEFAULT_TIMEZONE
  value: {{ .Values.app.defaultTimezone | quote }}
{{- /* Derived from the port the container actually publishes, so the two
       cannot drift into a Service that routes to a port nothing listens on. */}}
- name: SERVER_PORT
  value: {{ .Values.service.targetPort | quote }}
- name: LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
- name: VALIDATION_SPEC_CACHE_PATH
  value: {{ include "discovery.specCachePath" . | quote }}
{{- with $spec.url }}
- name: VALIDATION_SPEC_URL
  value: {{ . | quote }}
{{- end }}
- name: EMBEDDING_PROVIDER
  value: {{ .Values.embeddings.provider | quote }}
{{- if ne .Values.embeddings.provider "noop" }}
- name: EMBEDDING_MODEL
  value: {{ .Values.embeddings.model | quote }}
- name: EMBEDDING_DIMENSIONS
  value: {{ .Values.embeddings.dimensions | quote }}
{{- if not .Values.embeddings.endpoint }}
{{- fail (printf "%s: embeddings.provider is %q, so embeddings.endpoint is required - a provider with nowhere to call fails at the first publish, not at boot" .Chart.Name .Values.embeddings.provider) }}
{{- end }}
- name: EMBEDDING_ENDPOINT
  value: {{ .Values.embeddings.endpoint | quote }}
{{- end }}
- name: OTEL_EXPORTER
  value: {{ .Values.otel.exporter | quote }}
{{- if ne .Values.otel.exporter "none" }}
{{- if not .Values.otel.endpoint }}
{{- fail (printf "%s: otel.exporter is %q, so otel.endpoint is required - set it to the collector, e.g. http://otel-collector:4317" .Chart.Name .Values.otel.exporter) }}
{{- end }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.otel.endpoint | quote }}
{{- end }}
{{- with .Values.replicationTargets }}
- name: REPLICATION_TARGETS
  value: {{ join "," . | quote }}
{{- end }}
{{- with (include "oan-common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}
