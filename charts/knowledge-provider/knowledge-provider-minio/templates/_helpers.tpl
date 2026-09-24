{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-MINIO CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "knowledge-provider-minio.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "knowledge-provider-minio.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "knowledge-provider-minio.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "knowledge-provider-minio.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "knowledge-provider-minio.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "knowledge-provider-minio.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "knowledge-provider-minio.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "knowledge-provider-minio.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Root credentials, sourced from credentialsSecret - this chart renders no
Secret. Appends common.env (secretEnv / extraEnv).
*/}}
{{- define "knowledge-provider-minio.env" -}}
{{- $cred := .Values.credentialsSecret -}}
{{- if not $cred.name }}
{{- fail (printf "%s: credentialsSecret.name is required - this chart renders no Secrets, so it only references one" .Chart.Name) }}
{{- end }}
- name: MINIO_ROOT_USER
  valueFrom:
    secretKeyRef:
      name: {{ $cred.name }}
      key: {{ $cred.accessKeyKey }}
- name: MINIO_ROOT_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $cred.name }}
      key: {{ $cred.secretKeyKey }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

