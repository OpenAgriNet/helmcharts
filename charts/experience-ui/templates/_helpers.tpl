{{/*
# EXPERIENCE-UI CHART HELPERS
# Purpose: chart-local helpers that delegate to common.
*/}}

{{- define "experience-ui.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "experience-ui.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "experience-ui.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "experience-ui.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "experience-ui.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "experience-ui.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "experience-ui.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "experience-ui.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}


{{- define "experience-ui.configMapName" -}}
{{- printf "%s-config" (include "experience-ui.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end }}
