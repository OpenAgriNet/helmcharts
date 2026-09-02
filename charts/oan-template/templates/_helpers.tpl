{{/*
# ============================================================================
# OAN TEMPLATE CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to oan-common.
#
# When you copy this chart, rename every "oan-template." define below to your
# service name and update the matching include calls in the template YAML.
# Change only the LEFT side of each define - the oan-common include inside the
# body is the shared library you delegate to.
# ============================================================================
*/}}

{{- define "oan-template.name" -}}
{{- include "oan-common.name" . -}}
{{- end }}

{{- define "oan-template.fullname" -}}
{{- include "oan-common.fullname" . -}}
{{- end }}

{{- define "oan-template.chart" -}}
{{- include "oan-common.chart" . -}}
{{- end }}

{{- define "oan-template.labels" -}}
{{- include "oan-common.labels" . -}}
{{- end }}

{{- define "oan-template.selectorLabels" -}}
{{- include "oan-common.selectorLabels" . -}}
{{- end }}

{{- define "oan-template.serviceAccountName" -}}
{{- include "oan-common.serviceAccount.name" . -}}
{{- end }}

{{- define "oan-template.image" -}}
{{- include "oan-common.image" . -}}
{{- end }}

{{- define "oan-template.envConfigMapName" -}}
{{- include "oan-common.envConfigMapName" . -}}
{{- end }}

