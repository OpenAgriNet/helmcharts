{{/*
# ============================================================================
# KNOWLEDGE-PROVIDER-WORKER CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "knowledge-provider-worker.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "knowledge-provider-worker.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "knowledge-provider-worker.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "knowledge-provider-worker.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "knowledge-provider-worker.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "knowledge-provider-worker.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "knowledge-provider-worker.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "knowledge-provider-worker.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
Dependency waits, derived from this chart's own backing-service settings.
*/}}
{{- define "knowledge-provider-worker.waitFor" -}}
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
addresses, mount paths), as container env entries. Appends common.env
(secretEnv / extraEnv).
*/}}
{{- define "knowledge-provider-worker.env" -}}
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
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

