{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-API CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "knowledge-provider-api.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "knowledge-provider-api.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "knowledge-provider-api.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "knowledge-provider-api.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "knowledge-provider-api.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "knowledge-provider-api.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "knowledge-provider-api.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "knowledge-provider-api.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Dependency waits, derived from this chart's own backing-service settings.
*/}}
{{- define "knowledge-provider-api.waitFor" -}}
{{- $tcp := list -}}
{{- if .Values.waitFor.temporal }}
{{- $tcp = append $tcp (dict "name" "temporal" "host" .Values.temporal.host "port" .Values.temporal.port) -}}
{{- end -}}
{{- if .Values.waitFor.minio }}
{{- $tcp = append $tcp (dict "name" "minio" "host" .Values.minio.host "port" .Values.minio.port) -}}
{{- end -}}
{{- if .Values.waitFor.qdrant }}
{{- $tcp = append $tcp (dict "name" "qdrant" "host" .Values.vectorStore.host "port" .Values.vectorStore.port) -}}
{{- end -}}
{{- $tcp = concat $tcp (.Values.waitFor.extraTcp | default list) -}}
{{- $http := .Values.waitFor.extraHttp | default list -}}
{{- include "common.waitFor" (dict "ctx" . "tcp" $tcp "http" $http) -}}
{{- end }}

{{/*
Everything this chart derives from its structured values (backing-service
addresses, Keycloak issuer/JWKS, mount paths), as container env entries. These
take precedence over the envConfig ConfigMap, then common.env (secretEnv /
extraEnv) is appended.
*/}}
{{- define "knowledge-provider-api.env" -}}
- name: TEMPORAL_HOST
  value: {{ printf "%s:%v" .Values.temporal.host .Values.temporal.port | quote }}
- name: MINIO_ENDPOINT
  value: {{ printf "%s:%v" .Values.minio.host .Values.minio.port | quote }}
- name: MINIO_BUCKET
  value: {{ .Values.minio.bucket | quote }}
- name: VECTOR_DB_URL
  value: {{ printf "http://%s:%v" .Values.vectorStore.host .Values.vectorStore.port | quote }}
- name: VECTOR_DB_COLLECTION_NAME
  value: {{ .Values.vectorStore.collectionName | quote }}
- name: VECTOR_DB_TIMEOUT_SECONDS
  value: {{ .Values.vectorStore.timeoutSeconds | quote }}
- name: PROD_VECTOR_DB_URL
  value: {{ .Values.prodVectorStore.url | quote }}
- name: PROD_VECTOR_DB_COLLECTION_NAME
  value: {{ .Values.prodVectorStore.collectionName | quote }}
- name: PROD_VECTOR_DB_TIMEOUT_SECONDS
  value: {{ .Values.prodVectorStore.timeoutSeconds | quote }}
- name: DOCUMENT_DB_PATH
  value: "/data/documents.db"
- name: ALLOWED_FILE_PATHS
  value: "/app/books,/data/documents"
- name: AUTH_DISABLED
  value: {{ .Values.keycloak.authDisabled | quote }}
{{- if not .Values.keycloak.authDisabled }}
{{- if not .Values.keycloak.url }}
{{- fail (printf "%s: keycloak.url is required when keycloak.authDisabled is false - it is the base URL the issuer/JWKS URLs are derived from" .Chart.Name) }}
{{- end }}
- name: KEYCLOAK_ISSUER
  value: {{ printf "%s/realms/%s" (.Values.keycloak.url | trimSuffix "/") .Values.keycloak.realm | quote }}
- name: KEYCLOAK_JWKS_URL
  value: {{ printf "%s/realms/%s/protocol/openid-connect/certs" (.Values.keycloak.url | trimSuffix "/") .Values.keycloak.realm | quote }}
- name: KEYCLOAK_AUDIENCE
  value: {{ .Values.keycloak.audience | quote }}
- name: KEYCLOAK_JWT_LEEWAY_SECONDS
  value: {{ .Values.keycloak.jwtLeewaySeconds | quote }}
- name: KEYCLOAK_CLIENT_ID
  value: {{ .Values.keycloak.clientId | quote }}
- name: KEYCLOAK_ADMIN_BASE_URL
  value: {{ .Values.keycloak.adminBaseUrl | default .Values.keycloak.url | quote }}
- name: KEYCLOAK_ADMIN_REALM
  value: {{ .Values.keycloak.adminRealm | quote }}
- name: KEYCLOAK_ADMIN_TOKEN_REALM
  value: {{ .Values.keycloak.adminTokenRealm | quote }}
{{- with .Values.keycloak.adminUsername }}
- name: KEYCLOAK_ADMIN_USERNAME
  value: {{ . | quote }}
{{- end }}
{{- end }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

