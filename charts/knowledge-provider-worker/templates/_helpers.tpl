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
host/port of objectStore.endpoint, for the wait. Empty for GCS or no endpoint.
*/}}
{{- define "knowledge-provider-worker.objectStoreAddress" -}}
{{- $os := .Values.objectStore -}}
{{- if and (eq $os.type "s3") $os.endpoint -}}
{{- $u := urlParse $os.endpoint -}}
{{- $parts := splitList ":" $u.host -}}
{{- $port := ternary "443" "80" (eq $u.scheme "https") -}}
{{- if gt (len $parts) 1 }}{{ $port = last $parts }}{{ end -}}
{{- dict "host" (first $parts) "port" $port | toJson -}}
{{- end -}}
{{- end }}

{{/*
Dependency waits, derived from this chart's own backing-service settings.
*/}}
{{- define "knowledge-provider-worker.waitFor" -}}
{{- $tcp := list -}}
{{- if .Values.waitFor.database }}
{{- $tcp = append $tcp (dict "name" "database" "host" .Values.database.host "port" .Values.database.port) -}}
{{- end -}}
{{- if .Values.waitFor.temporal }}
{{- $tcp = append $tcp (dict "name" "temporal" "host" .Values.temporal.host "port" .Values.temporal.port) -}}
{{- end -}}
{{- with (include "knowledge-provider-worker.objectStoreAddress" . ) }}
{{- if $.Values.waitFor.objectStore }}
{{- $addr := fromJson . -}}
{{- $tcp = append $tcp (dict "name" "object-store" "host" $addr.host "port" $addr.port) -}}
{{- end -}}
{{- end -}}
{{- if .Values.waitFor.qdrant }}
{{- $tcp = append $tcp (dict "name" "qdrant" "host" .Values.vectorStore.host "port" .Values.vectorStore.port) -}}
{{- end -}}
{{- $tcp = concat $tcp (.Values.waitFor.extraTcp | default list) -}}
{{- $http := .Values.waitFor.extraHttp | default list -}}
{{- include "common.waitFor" (dict "ctx" . "tcp" $tcp "http" $http) -}}
{{- end }}

{{/*
Backing-service env shared with knowledge-provider-worker: PostgreSQL, Temporal,
object store and Qdrant. Keep the two charts' copies identical.
*/}}
{{- define "knowledge-provider-worker.backingEnv" -}}
{{- $db := .Values.database -}}
{{- if not $db.host }}
{{- fail (printf "%s: database.host is required - the PostgreSQL service, e.g. knowledge-provider-db-rw" .Chart.Name) }}
{{- end }}
{{- if not $db.passwordSecret.name }}
{{- fail (printf "%s: database.passwordSecret.name is required - this chart renders no Secrets" .Chart.Name) }}
{{- end }}
- name: DATABASE_URL
  value: {{ printf "postgresql://%s@%s:%v/%s?sslmode=%s" $db.user $db.host $db.port $db.name $db.sslmode | quote }}
{{- /* libpq reads PGPASSWORD, so the URL needs no encoding. */}}
- name: PGPASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $db.passwordSecret.name }}
      key: {{ $db.passwordSecret.key }}
- name: TEMPORAL_HOST
  value: {{ printf "%s:%v" .Values.temporal.host .Values.temporal.port | quote }}
{{- $os := .Values.objectStore }}
{{- if not $os.bucket }}
{{- fail (printf "%s: objectStore.bucket is required" .Chart.Name) }}
{{- end }}
- name: OBJECT_STORE_TYPE
  value: {{ $os.type | quote }}
{{- with $os.endpoint }}
- name: OBJECT_STORE_ENDPOINT
  value: {{ . | quote }}
{{- end }}
- name: OBJECT_STORE_BUCKET
  value: {{ $os.bucket | quote }}
- name: OBJECT_STORE_CREDENTIALS_TYPE
  value: {{ $os.credentialsType | quote }}
{{- with $os.region }}
- name: OBJECT_STORE_REGION
  value: {{ . | quote }}
{{- end }}
- name: OBJECT_STORE_CREATE_BUCKET
  value: {{ $os.createBucket | quote }}
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
{{- end }}

{{/*
Everything this chart derives from its structured values (backing services),
as container env entries. Appends common.env (secretEnv / extraEnv).
*/}}
{{- define "knowledge-provider-worker.env" -}}
{{ include "knowledge-provider-worker.backingEnv" . | trim }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}
