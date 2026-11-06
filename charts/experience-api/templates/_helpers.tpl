{{/*
# EXPERIENCE-API CHART HELPERS
# Purpose: chart-local helpers that delegate to common.
*/}}

{{- define "experience-api.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "experience-api.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "experience-api.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "experience-api.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "experience-api.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "experience-api.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "experience-api.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "experience-api.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}


{{/*
The app's settings, from the structured values. Both required ones fail the
render here rather than as a crash-looping pod.
*/}}
{{- define "experience-api.env" -}}
{{- if not .Values.senderId }}
{{- fail (printf "%s: senderId is required - the app refuses to start without EXPERIENCE_API_SENDER_ID" .Chart.Name) }}
{{- end }}
{{- if not .Values.dssBaseUrl }}
{{- fail (printf "%s: dssBaseUrl is required - the app's default, localhost, is never right in a pod" .Chart.Name) }}
{{- end }}
- name: EXPERIENCE_API_SENDER_ID
  value: {{ .Values.senderId | quote }}
- name: EXPERIENCE_API_DSS_BASE_URL
  value: {{ .Values.dssBaseUrl | quote }}
- name: EXPERIENCE_API_CHANNEL
  value: {{ .Values.channel | quote }}
- name: EXPERIENCE_API_MAX_CHARACTERS
  value: {{ .Values.maxCharacters | quote }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}
