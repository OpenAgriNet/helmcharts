{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-QDRANT CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "qdrant.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "qdrant.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "qdrant.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "qdrant.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "qdrant.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "qdrant.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "qdrant.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "qdrant.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Optional API key, sourced from apiKeySecret. Skipped entirely when
apiKeySecret.name is empty, matching compose's no-auth default. Appends
common.env (secretEnv / extraEnv).
*/}}
{{- define "qdrant.env" -}}
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

