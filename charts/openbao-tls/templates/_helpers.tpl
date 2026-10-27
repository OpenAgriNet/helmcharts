{{- define "openbao-tls.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: oan
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Every name OpenBao is reached by, in short and fully qualified form: the
Service (ESO), the active and standby Services (the snapshot job, in HA), and
each pod through the headless Service (raft retry_join).
*/}}
{{- define "openbao-tls.dnsNames" -}}
{{- $ns := .Release.Namespace -}}
{{- $names := list "localhost" -}}
{{- range $svc := list .Values.serviceName (printf "%s-active" .Values.serviceName) (printf "%s-standby" .Values.serviceName) (printf "%s-internal" .Values.serviceName) (printf "*.%s-internal" .Values.serviceName) -}}
{{- $names = concat $names (list $svc (printf "%s.%s" $svc $ns) (printf "%s.%s.svc" $svc $ns) (printf "%s.%s.svc.cluster.local" $svc $ns)) -}}
{{- end -}}
{{- toYaml (concat $names .Values.server.extraDnsNames) -}}
{{- end }}
