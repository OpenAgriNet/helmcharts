{{/*
# ============================================================================
# REGISTRY (SUNBIRD RC) CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to oan-common, plus the database,
#          Keycloak and schema wiring the registry needs.
# ============================================================================
*/}}

{{- define "registry.name" -}}
{{- include "oan-common.name" . -}}
{{- end }}

{{- define "registry.fullname" -}}
{{- include "oan-common.fullname" . -}}
{{- end }}

{{- define "registry.labels" -}}
{{- include "oan-common.labels" . -}}
{{- end }}

{{- define "registry.selectorLabels" -}}
{{- include "oan-common.selectorLabels" . -}}
{{- end }}

{{- define "registry.serviceAccountName" -}}
{{- include "oan-common.serviceAccount.name" . -}}
{{- end }}

{{- define "registry.image" -}}
{{- include "oan-common.image" . -}}
{{- end }}

{{- define "registry.envConfigMapName" -}}
{{- include "oan-common.envConfigMapName" . -}}
{{- end }}

{{/*
JDBC URL for the registry database.
*/}}
{{- define "registry.jdbcUrl" -}}
{{- $db := .Values.database -}}
{{- printf "jdbc:postgresql://%s:%v/%s" $db.host $db.port $db.name -}}
{{- end }}

{{/*
Name of the ConfigMap holding the entity schemas. An existing ConfigMap wins,
so schemas can be managed outside this chart.
*/}}
{{- define "registry.schemasConfigMapName" -}}
{{- if .Values.schemas.existingConfigMap -}}
{{- .Values.schemas.existingConfigMap -}}
{{- else -}}
{{- printf "%s-schemas" (include "registry.fullname" .) -}}
{{- end -}}
{{- end }}

{{/*
Emits "true" when this chart renders the schemas ConfigMap itself.
*/}}
{{- define "registry.renderSchemasConfigMap" -}}
{{- if not .Values.schemas.existingConfigMap -}}
{{- true -}}
{{- end -}}
{{- end }}

{{/*
The schema files shipped with the chart, as ConfigMap data entries. The registry
reads every .json under its schema directory as an entity definition, so an
empty set means a registry that knows about no entities at all.
*/}}
{{- define "registry.schemasData" -}}
{{- $files := .Files.Glob .Values.schemas.filesGlob -}}
{{- if and (not $files) (not .Values.schemas.inline) -}}
{{- fail (printf "%s: no schema files matched %q in the chart and schemas.inline is empty. The registry needs at least one entity definition." .Chart.Name .Values.schemas.filesGlob) -}}
{{- end -}}
{{- range $path, $_ := $files }}
{{ base $path }}: |-
  {{- $.Files.Get $path | nindent 2 }}
{{- end }}
{{- range $name, $content := .Values.schemas.inline }}
{{ $name }}: |-
  {{- $content | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Environment the registry needs beyond the flags in envConfig: everything derived
from the database and Keycloak settings, plus the three secret values.

Mirrors registry/docker-compose.yml. `sunbird_sso_url` and OAUTH2_RESOURCES_0_URI
must agree with the issuer Keycloak actually puts in its tokens, or every request
is rejected as unauthorised.
*/}}
{{- define "registry.env" -}}
{{- $db := .Values.database -}}
{{- $kc := .Values.keycloak -}}
{{- if not $db.host }}
{{- fail (printf "%s: database.host is required - point it at the PostgreSQL primary service, e.g. registry-db-rw" .Chart.Name) }}
{{- end }}
{{- if not $db.passwordSecret.name }}
{{- fail (printf "%s: database.passwordSecret.name is required - this chart renders no passwords" .Chart.Name) }}
{{- end }}
{{- if not $kc.url }}
{{- fail (printf "%s: keycloak.url is required, including the /auth context path, e.g. http://keycloak:8080/auth" .Chart.Name) }}
{{- end }}
{{- if not $kc.adminClientSecret.name }}
{{- fail (printf "%s: keycloak.adminClientSecret.name is required. Regenerate the admin-api client secret in the Keycloak console after the realm import, then store it." .Chart.Name) }}
{{- end }}
{{- if and .Values.keycloakUserSetPassword (not .Values.defaultUserPasswordSecret.name) }}
{{- fail (printf "%s: keycloakUserSetPassword is true, so defaultUserPasswordSecret.name is required - it is the password the registry sets on Keycloak users it creates" .Chart.Name) }}
{{- end }}
{{- $kcUrl := $kc.url | trimSuffix "/" }}
- name: connectionInfo_uri
  value: {{ include "registry.jdbcUrl" . | quote }}
- name: connectionInfo_username
  value: {{ $db.user | quote }}
- name: connectionInfo_password
  valueFrom:
    secretKeyRef:
      name: {{ $db.passwordSecret.name }}
      key: {{ $db.passwordSecret.key }}
- name: authentication_enabled
  value: {{ .Values.authenticationEnabled | quote }}
- name: sunbird_sso_realm
  value: {{ $kc.realm | quote }}
- name: sunbird_sso_url
  value: {{ $kcUrl | quote }}
- name: OAUTH2_RESOURCES_0_URI
  value: {{ printf "%s/realms/%s" $kcUrl $kc.realm | quote }}
- name: OAUTH2_RESOURCES_0_PROPERTIES_ROLES_PATH
  value: {{ $kc.rolesPath | quote }}
- name: identity_provider
  value: {{ $kc.identityProvider | quote }}
- name: sunbird_sso_admin_client_id
  value: {{ $kc.adminClientId | quote }}
- name: sunbird_sso_client_id
  value: {{ $kc.clientId | quote }}
- name: sunbird_sso_admin_client_secret
  valueFrom:
    secretKeyRef:
      name: {{ $kc.adminClientSecret.name }}
      key: {{ $kc.adminClientSecret.key }}
- name: sunbird_keycloak_user_set_password
  value: {{ .Values.keycloakUserSetPassword | quote }}
{{- with .Values.defaultUserPasswordSecret.name }}
- name: sunbird_keycloak_user_password
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $.Values.defaultUserPasswordSecret.key }}
{{- end }}
{{- with (include "oan-common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

{{/*
Dependency waits, derived from this chart's own settings so there is nothing to
keep in sync.

The Keycloak check is HTTP against the realm endpoint, not TCP against the port:
the realm is imported during Keycloak's first start, so the port opens well
before the realm the registry needs actually exists. A TCP check would pass too
early and the registry would start against a Keycloak that cannot yet issue it a
usable token.
*/}}
{{- define "registry.waitFor" -}}
{{- $tcp := list -}}
{{- $http := list -}}
{{- if .Values.waitFor.database }}
{{- $tcp = append $tcp (dict "name" "database" "host" .Values.database.host "port" .Values.database.port) -}}
{{- end -}}
{{- if .Values.waitFor.keycloak }}
{{- $url := printf "%s/realms/%s" (.Values.keycloak.url | trimSuffix "/") .Values.keycloak.realm -}}
{{- $http = append $http (dict "name" "keycloak" "url" $url) -}}
{{- end -}}
{{- $tcp = concat $tcp (.Values.waitFor.extraTcp | default list) -}}
{{- $http = concat $http (.Values.waitFor.extraHttp | default list) -}}
{{- include "oan-common.waitFor" (dict "ctx" . "tcp" $tcp "http" $http) -}}
{{- end }}
