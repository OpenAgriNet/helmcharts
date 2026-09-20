{{/*
Chart-local helpers. This chart does not depend on `common`: it renders no
workload, so none of the image, probe, resource or service-account helpers
apply, and pulling the library in would make a two-template chart carry a
dependency it never calls.
*/}}
{{- define "cert-manager-issuers.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "cert-manager-issuers.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name (.Chart.Version | replace "+" "_") | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/name: {{ include "cert-manager-issuers.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Chart.AppVersion }}
app.kubernetes.io/version: {{ . | quote }}
{{- end }}
{{- end }}
