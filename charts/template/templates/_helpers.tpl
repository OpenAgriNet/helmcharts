{{/*
# ============================================================================
# OAN TEMPLATE CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
#
# When you copy this chart, rename every "template." define below to your
# service name and update the matching include calls in the template YAML.
# Change only the LEFT side of each define - the common include inside the
# body is the shared library you delegate to.
# ============================================================================
*/}}

{{- define "template.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "template.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "template.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "template.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "template.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "template.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "template.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "template.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

