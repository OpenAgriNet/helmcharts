{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-UI CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "knowledge-provider-ui.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "knowledge-provider-ui.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "knowledge-provider-ui.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "knowledge-provider-ui.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "knowledge-provider-ui.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "knowledge-provider-ui.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "knowledge-provider-ui.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "knowledge-provider-ui.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

