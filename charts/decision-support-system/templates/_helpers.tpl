{{/*
# ============================================================================
# DECISION-SUPPORT-SYSTEM CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to common, plus the model/network/
#          schema-pack/evidence env wiring this service needs.
# ============================================================================
*/}}

{{- define "decision-support-system.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "decision-support-system.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "decision-support-system.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "decision-support-system.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "decision-support-system.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "decision-support-system.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "decision-support-system.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Emits "true" when at least one of the four agents is bound to an azure:...
model, in which case AZURE_OPENAI_ENDPOINT/AZURE_OPENAI_API_KEY become
required - the SDK-level check in entrypoint/composition.py::_resolve_model
raises at boot on either being missing, one agent at a time. Checking all four
at render time surfaces every missing one at once, before anything is applied.
*/}}
{{- define "decision-support-system.usesAzure" -}}
{{- $m := .Values.models -}}
{{- if or (hasPrefix "azure:" $m.intent) (hasPrefix "azure:" $m.moderation) (hasPrefix "azure:" $m.planner) (hasPrefix "azure:" $m.composer) -}}
{{- true -}}
{{- end -}}
{{- end }}

{{/*
Everything the service needs that this chart derives from its structured
values, as container env entries. These take precedence over the envConfig
ConfigMap injected with envFrom.
*/}}
{{- define "decision-support-system.env" -}}
{{- $m := .Values.models -}}
{{- $net := .Values.network -}}
{{- $azure := .Values.azureOpenai -}}
{{- $openai := .Values.openai -}}
{{- $packs := .Values.schemaPacks -}}
{{- $tracing := .Values.tracing -}}
{{- if include "decision-support-system.usesAzure" . }}
{{- if not $azure.endpoint }}
{{- fail (printf "%s: a models.* value uses the azure: prefix, so azureOpenai.endpoint is required - the SDK reads AZURE_OPENAI_ENDPOINT directly and raises at boot without it" .Chart.Name) }}
{{- end }}
{{- if not $azure.apiKeySecret.name }}
{{- fail (printf "%s: a models.* value uses the azure: prefix, so azureOpenai.apiKeySecret.{name,key} is required - it becomes AZURE_OPENAI_API_KEY. This chart renders no Secrets; create one out of band." .Chart.Name) }}
{{- end }}
{{- end }}
{{- if and $net.discoveryBaseUrl (not $net.invocationBaseUrl) }}
{{- fail (printf "%s: network.discoveryBaseUrl is set but network.invocationBaseUrl is not. Settings.network_enabled requires both or neither - a partial config does not fail the boot, it quietly answers every turn no_match, which reads as a broken network rather than an intentionally unwired one." .Chart.Name) }}
{{- end }}
{{- if and $net.invocationBaseUrl (not $net.discoveryBaseUrl) }}
{{- fail (printf "%s: network.invocationBaseUrl is set but network.discoveryBaseUrl is not. Settings.network_enabled requires both or neither - see the discoveryBaseUrl check above for why this is caught here rather than left to degrade silently." .Chart.Name) }}
{{- end }}
- name: DSS_INTENT_MODEL
  value: {{ $m.intent | quote }}
- name: DSS_MODERATION_MODEL
  value: {{ $m.moderation | quote }}
- name: DSS_PLANNER_MODEL
  value: {{ $m.planner | quote }}
- name: DSS_COMPOSER_MODEL
  value: {{ $m.composer | quote }}
{{- with $azure.endpoint }}
- name: AZURE_OPENAI_ENDPOINT
  value: {{ . | quote }}
{{- end }}
{{- with $azure.apiKeySecret.name }}
- name: AZURE_OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $azure.apiKeySecret.key }}
{{- end }}
{{- with $openai.baseUrl }}
- name: OPENAI_BASE_URL
  value: {{ . | quote }}
{{- end }}
{{- with $openai.apiKeySecret.name }}
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $openai.apiKeySecret.key }}
{{- end }}
{{- with $net.discoveryBaseUrl }}
- name: DSS_DISCOVERY_BASE_URL
  value: {{ . | quote }}
- name: DSS_INVOCATION_BASE_URL
  value: {{ $net.invocationBaseUrl | quote }}
- name: DSS_NETWORK_SENDER_ID
  value: {{ $net.senderId | quote }}
- name: DSS_NETWORK_RECEIVER_ID
  value: {{ $net.receiverId | quote }}
- name: DSS_DISCOVERY_RADIUS_M
  value: {{ $net.radiusMeters | quote }}
{{- end }}
- name: DSS_SCHEMA_PACK_DIR
  value: {{ $packs.mountPath | quote }}
- name: DSS_SCHEMA_PACK_REF
  value: {{ $packs.ref | quote }}
{{- with $packs.githubTokenSecret.name }}
- name: GITHUB_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $packs.githubTokenSecret.key }}
{{- end }}
- name: DSS_EVIDENCE_DIR
  value: {{ .Values.evidence.mountPath | quote }}
{{- with $tracing.otlpEndpoint }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ . | quote }}
{{- end }}
- name: OTEL_METRICS_EXPORTER
  value: {{ $tracing.metricsExporter | quote }}
{{- with $tracing.headersSecret.name }}
- name: OTEL_EXPORTER_OTLP_HEADERS
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $tracing.headersSecret.key }}
{{- end }}
- name: DSS_LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}
