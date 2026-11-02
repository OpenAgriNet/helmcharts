{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-KEYCLOAK CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "knowledge-provider-keycloak.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "knowledge-provider-keycloak.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "knowledge-provider-keycloak.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "knowledge-provider-keycloak.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "knowledge-provider-keycloak.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "knowledge-provider-keycloak.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "knowledge-provider-keycloak.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "knowledge-provider-keycloak.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
JDBC URL assembled from database.host/port/name/sslmode. Credentials are
separate env vars (KC_DB_USERNAME/KC_DB_PASSWORD) - Keycloak does not read
them from the URL.
*/}}
{{- define "knowledge-provider-keycloak.databaseURL" -}}
{{- $db := .Values.database -}}
{{- printf "jdbc:postgresql://%s:%v/%s?sslmode=%s" $db.host $db.port $db.name $db.sslmode -}}
{{- end }}

{{/*
Dependency waits, derived from this chart's own database setting.
*/}}
{{- define "knowledge-provider-keycloak.waitFor" -}}
{{- $tcp := list -}}
{{- if .Values.waitFor.database }}
{{- $tcp = append $tcp (dict "name" "database" "host" .Values.database.host "port" .Values.database.port) -}}
{{- end -}}
{{- $tcp = concat $tcp (.Values.waitFor.extraTcp | default list) -}}
{{- $http := .Values.waitFor.extraHttp | default list -}}
{{- include "common.waitFor" (dict "ctx" . "tcp" $tcp "http" $http) -}}
{{- end }}

{{/*
Everything this chart derives from its structured values (DB connection,
admin bootstrap, hostname, SMTP), as container env entries. Appends
common.env (secretEnv / extraEnv) for anything not covered here.
*/}}
{{- define "knowledge-provider-keycloak.env" -}}
{{- $db := .Values.database -}}
{{- if not $db.host }}
{{- fail (printf "%s: database.host is required - point it at the Postgres primary service, e.g. knowledge-provider-keycloak-db-rw" .Chart.Name) }}
{{- end }}
{{- if not $db.passwordSecret.name }}
{{- fail (printf "%s: database.passwordSecret.name is required - this chart renders no Secrets, so it only references one" .Chart.Name) }}
{{- end }}
- name: KC_DB
  value: postgres
- name: KC_DB_URL
  value: {{ include "knowledge-provider-keycloak.databaseURL" . | quote }}
- name: KC_DB_USERNAME
  value: {{ $db.user | quote }}
- name: KC_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $db.passwordSecret.name }}
      key: {{ $db.passwordSecret.key }}
{{- if not .Values.admin.passwordSecret.name }}
{{- fail (printf "%s: admin.passwordSecret.name is required - this chart renders no Secrets, so it only references one" .Chart.Name) }}
{{- end }}
- name: KC_BOOTSTRAP_ADMIN_USERNAME
  value: {{ .Values.admin.username | quote }}
- name: KC_BOOTSTRAP_ADMIN_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.admin.passwordSecret.name }}
      key: {{ .Values.admin.passwordSecret.key }}
{{- if not .Values.hostname }}
{{- fail (printf "%s: hostname is required - it is KC_HOSTNAME, and issuer/redirect URIs are wrong without it" .Chart.Name) }}
{{- end }}
- name: KC_HOSTNAME
  value: {{ .Values.hostname | quote }}
- name: KC_HOSTNAME_STRICT
  value: "false"
- name: KC_HTTP_ENABLED
  value: "true"
- name: KC_PROXY_HEADERS
  value: "xforwarded"
- name: KC_HTTP_RELATIVE_PATH
  value: {{ .Values.httpRelativePath | quote }}
- name: KC_HEALTH_ENABLED
  value: "true"
{{- with .Values.smtp }}
{{- if .host }}
- name: KC_SPI_EMAIL_SENDER_HOST
  value: {{ .host | quote }}
- name: KC_SPI_EMAIL_SENDER_PORT
  value: {{ .port | quote }}
- name: KC_SPI_EMAIL_SENDER_FROM
  value: {{ .from | quote }}
- name: KC_SPI_EMAIL_SENDER_FROM_DISPLAY_NAME
  value: {{ .fromDisplayName | quote }}
- name: KC_SPI_EMAIL_SENDER_AUTH
  value: {{ .auth | quote }}
- name: KC_SPI_EMAIL_SENDER_STARTTLS
  value: {{ .starttls | quote }}
- name: KC_SPI_EMAIL_SENDER_SSL
  value: {{ .ssl | quote }}
{{- with .username }}
- name: KC_SPI_EMAIL_SENDER_USER
  value: {{ . | quote }}
{{- end }}
{{- with .passwordSecret.name }}
- name: KC_SPI_EMAIL_SENDER_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $.Values.smtp.passwordSecret.key }}
{{- end }}
{{- end }}
{{- end }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

