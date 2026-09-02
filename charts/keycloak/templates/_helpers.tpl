{{/*
# ============================================================================
# KEYCLOAK (SUNBIRD RC) CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to oan-common, plus the realm-import
#          and legacy-Keycloak database wiring this chart needs.
# ============================================================================
*/}}

{{- define "keycloak.name" -}}
{{- include "oan-common.name" . -}}
{{- end }}

{{- define "keycloak.fullname" -}}
{{- include "oan-common.fullname" . -}}
{{- end }}

{{- define "keycloak.labels" -}}
{{- include "oan-common.labels" . -}}
{{- end }}

{{- define "keycloak.selectorLabels" -}}
{{- include "oan-common.selectorLabels" . -}}
{{- end }}

{{- define "keycloak.serviceAccountName" -}}
{{- include "oan-common.serviceAccount.name" . -}}
{{- end }}

{{- define "keycloak.image" -}}
{{- include "oan-common.image" . -}}
{{- end }}

{{- define "keycloak.envConfigMapName" -}}
{{- include "oan-common.envConfigMapName" . -}}
{{- end }}

{{/*
The base URL other services use to reach this Keycloak, including the /auth
context path the legacy distribution serves under. The registry's issuer check
compares against exactly this, so it must match what the registry is given.
*/}}
{{- define "keycloak.url" -}}
{{- printf "http://%s.%s.svc.cluster.local:%v/auth" (include "keycloak.fullname" .) .Release.Namespace .Values.service.port -}}
{{- end }}

{{/*
Name of the ConfigMap holding the realm export.

The chart renders its own by default. An existingConfigMap wins, for the case
where the realm is managed outside the chart entirely.
*/}}
{{- define "keycloak.realmConfigMapName" -}}
{{- if .Values.realmImport.existingConfigMap -}}
{{- .Values.realmImport.existingConfigMap -}}
{{- else -}}
{{- printf "%s-realm" (include "keycloak.fullname" .) -}}
{{- end -}}
{{- end }}

{{/*
Emits "true" when this chart renders the realm ConfigMap itself, i.e. whenever
the realm is not being supplied by an out-of-band ConfigMap.
*/}}
{{- define "keycloak.renderRealmConfigMap" -}}
{{- if and .Values.realmImport.enabled (not .Values.realmImport.existingConfigMap) -}}
{{- true -}}
{{- end -}}
{{- end }}

{{/*
The realm JSON: an inline override if given, otherwise the file shipped in the
chart under files/.
*/}}
{{- define "keycloak.realmJson" -}}
{{- if .Values.realmImport.realmJson -}}
{{- .Values.realmImport.realmJson -}}
{{- else -}}
{{- $json := .Files.Get .Values.realmImport.file -}}
{{- if not $json -}}
{{- fail (printf "%s: realmImport is enabled but %q is not present in the chart, and neither realmImport.realmJson nor realmImport.existingConfigMap is set." .Chart.Name .Values.realmImport.file) -}}
{{- end -}}
{{- $json -}}
{{- end -}}
{{- end }}

{{/*
Path the realm file is mounted at, which is what KEYCLOAK_IMPORT points to.
*/}}
{{- define "keycloak.realmImportPath" -}}
{{- printf "%s/%s" (.Values.realmImport.mountPath | trimSuffix "/") .Values.realmImport.fileName -}}
{{- end }}

{{/*
Environment for the legacy (WildFly) Keycloak distribution. This image predates
Keycloak's KC_* variables, so it takes DB_VENDOR/DB_ADDR and friends. Values
mirror the compose stack.
*/}}
{{- define "keycloak.env" -}}
{{- $db := .Values.database }}
{{- if not $db.host }}
{{- fail (printf "%s: database.host is required - point it at the PostgreSQL primary service, e.g. registry-db-rw" .Chart.Name) }}
{{- end }}
{{- if not $db.passwordSecret.name }}
{{- fail (printf "%s: database.passwordSecret.name is required - this chart renders no passwords" .Chart.Name) }}
{{- end }}
{{- if not .Values.admin.passwordSecret.name }}
{{- fail (printf "%s: admin.passwordSecret.name is required - this chart renders no passwords" .Chart.Name) }}
{{- end }}
- name: DB_VENDOR
  value: {{ $db.vendor | quote }}
- name: DB_ADDR
  value: {{ $db.host | quote }}
- name: DB_PORT
  value: {{ $db.port | quote }}
- name: DB_DATABASE
  value: {{ $db.name | quote }}
- name: DB_USER
  value: {{ $db.user | quote }}
{{- with $db.schema }}
- name: DB_SCHEMA
  value: {{ . | quote }}
{{- end }}
- name: DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $db.passwordSecret.name }}
      key: {{ $db.passwordSecret.key }}
- name: KEYCLOAK_USER
  value: {{ .Values.admin.username | quote }}
- name: KEYCLOAK_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.admin.passwordSecret.name }}
      key: {{ .Values.admin.passwordSecret.key }}
- name: PROXY_ADDRESS_FORWARDING
  value: {{ .Values.proxyAddressForwarding | quote }}
{{- if .Values.realmImport.enabled }}
- name: KEYCLOAK_IMPORT
  value: {{ include "keycloak.realmImportPath" . | quote }}
{{- end }}
{{- with (include "oan-common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

{{/*
Dependency waits, derived from this chart's own settings so there is nothing to
keep in sync: the host comes from database.host, which is the same value the
container connects to.
*/}}
{{- define "keycloak.waitFor" -}}
{{- $tcp := list -}}
{{- if .Values.waitFor.database }}
{{- $tcp = append $tcp (dict "name" "database" "host" .Values.database.host "port" .Values.database.port) -}}
{{- end -}}
{{- $tcp = concat $tcp (.Values.waitFor.extraTcp | default list) -}}
{{- include "oan-common.waitFor" (dict "ctx" . "tcp" $tcp "http" (.Values.waitFor.extraHttp | default list)) -}}
{{- end }}
