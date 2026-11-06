{{/*
# ============================================================================
# PHOTON CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "photon.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "photon.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "photon.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "photon.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "photon.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "photon.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "photon.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{- define "photon.claimName" -}}
{{- .Values.persistence.existingClaim | default (printf "%s-data" (include "photon.fullname" .)) -}}
{{- end }}

{{- define "photon.jarName" -}}
{{- printf "photon-%s.jar" .Values.photon.version -}}
{{- end }}

{{- define "photon.jarUrl" -}}
{{- .Values.photon.jarUrl | default (printf "https://github.com/komoot/photon/releases/download/%s/%s" .Values.photon.version (include "photon.jarName" .)) -}}
{{- end }}

{{- define "photon.indexImage" -}}
{{- $i := .Values.index.image -}}
{{- if $i.registry }}{{ $i.registry }}/{{ end }}{{ $i.repository }}:{{ $i.tag }}
{{- end }}
