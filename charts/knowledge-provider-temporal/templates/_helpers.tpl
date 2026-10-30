{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-TEMPORAL CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "knowledge-provider-temporal.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "knowledge-provider-temporal.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "knowledge-provider-temporal.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "knowledge-provider-temporal.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "knowledge-provider-temporal.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "knowledge-provider-temporal.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "knowledge-provider-temporal.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "knowledge-provider-temporal.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Dependency waits, derived from this chart's own database setting.
*/}}
{{- define "knowledge-provider-temporal.waitFor" -}}
{{- $tcp := list -}}
{{- if .Values.waitFor.database }}
{{- $tcp = append $tcp (dict "name" "database" "host" .Values.database.host "port" .Values.database.port) -}}
{{- end -}}
{{- $tcp = concat $tcp (.Values.waitFor.extraTcp | default list) -}}
{{- $http := .Values.waitFor.extraHttp | default list -}}
{{- include "common.waitFor" (dict "ctx" . "tcp" $tcp "http" $http) -}}
{{- end }}

{{/*
Everything this chart derives from its structured database value, as
container env entries (the temporalio/auto-setup image's own contract:
DB/DB_PORT/POSTGRES_USER/POSTGRES_PWD/POSTGRES_SEEDS/DBNAME/VISIBILITY_DBNAME).
Appends common.env (secretEnv / extraEnv).
*/}}
{{- define "knowledge-provider-temporal.env" -}}
{{- $db := .Values.database -}}
{{- if not $db.host }}
{{- fail (printf "%s: database.host is required - point it at the Postgres primary service, e.g. knowledge-provider-temporal-db-rw" .Chart.Name) }}
{{- end }}
{{- if not $db.passwordSecret.name }}
{{- fail (printf "%s: database.passwordSecret.name is required - this chart renders no Secrets, so it only references one" .Chart.Name) }}
{{- end }}
- name: DB
  value: postgres12
- name: DB_PORT
  value: {{ $db.port | quote }}
- name: POSTGRES_SEEDS
  value: {{ $db.host | quote }}
- name: POSTGRES_USER
  value: {{ $db.user | quote }}
- name: POSTGRES_PWD
  valueFrom:
    secretKeyRef:
      name: {{ $db.passwordSecret.name }}
      key: {{ $db.passwordSecret.key }}
- name: DBNAME
  value: {{ $db.name | quote }}
- name: VISIBILITY_DBNAME
  value: {{ $db.visibilityName | quote }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

