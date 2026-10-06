{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-MINIO CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "minio.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "minio.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "minio.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "minio.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "minio.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "minio.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "minio.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "minio.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Root credentials, sourced from credentialsSecret - this chart renders no
Secret. Appends common.env (secretEnv / extraEnv).
*/}}
{{- define "minio.env" -}}
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

