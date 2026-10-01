{{/*
# ============================================================================
# OAN DASHBOARDS CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "dashboards.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "dashboards.labels" -}}
{{- include "common.labels" . -}}
{{- end }}
