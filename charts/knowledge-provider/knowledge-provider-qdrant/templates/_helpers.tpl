{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-QDRANT CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "knowledge-provider-qdrant.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "knowledge-provider-qdrant.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "knowledge-provider-qdrant.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "knowledge-provider-qdrant.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "knowledge-provider-qdrant.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "knowledge-provider-qdrant.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "knowledge-provider-qdrant.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "knowledge-provider-qdrant.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Optional API key, sourced from apiKeySecret. Skipped entirely when
apiKeySecret.name is empty, matching compose's no-auth default. Appends
common.env (secretEnv / extraEnv).
*/}}
{{- define "knowledge-provider-qdrant.env" -}}
{{- with .Values.apiKeySecret.name }}
- name: QDRANT__SERVICE__API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $.Values.apiKeySecret.key }}
{{- end }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

