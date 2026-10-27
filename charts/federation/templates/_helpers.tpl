{{- define "federation.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "federation.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "federation.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "federation.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "federation.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "federation.selectorLabels" -}}
app.kubernetes.io/name: {{ include "federation.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "federation.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "federation.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "federation.documentsConfigMap" -}}
{{- default (printf "%s-documents" (include "federation.fullname" .)) .Values.documentsConfigMap -}}
{{- end -}}

{{- define "federation.image" -}}
{{- $i := .image -}}
{{- printf "%s/%s:%s" $i.registry $i.repository $i.tag -}}
{{- end -}}

{{- /* Required values, checked together so one render names them all. */ -}}
{{- define "federation.require" -}}
{{- $missing := list -}}
{{- if not .Values.networkId }}{{ $missing = append $missing "networkId" }}{{ end -}}
{{- if not .Values.registryUrl }}{{ $missing = append $missing "registryUrl" }}{{ end -}}
{{- if not .Values.keys.existingSecret.name }}{{ $missing = append $missing "keys.existingSecret.name" }}{{ end -}}
{{- if $missing -}}
{{- fail (printf "%s: %s must be set.\n\n  The descriptor is built from this network's registry entry and signed with\n  the key that entry publishes -- so the id, the registry and the Secret the\n  adapter reads are all needed before anything can be signed." .Chart.Name (join ", " $missing)) -}}
{{- end -}}
{{- end -}}
