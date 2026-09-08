{{/*
# ============================================================================
# OAN POSTGRESQL CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to oan-common, plus the CNPG-specific
#          naming and object-store logic this chart needs.
# ============================================================================
*/}}

{{/*
Cluster name. Drives the CNPG service names <name>-rw / <name>-ro / <name>-r,
which other charts connect to, so it should be short and stable - set
fullnameOverride (e.g. "registry-db") rather than relying on <release>-<chart>.
*/}}
{{- define "postgresql-cnpg.fullname" -}}
{{- include "oan-common.fullname" . -}}
{{- end }}

{{- define "postgresql-cnpg.name" -}}
{{- include "oan-common.name" . -}}
{{- end }}

{{- define "postgresql-cnpg.labels" -}}
{{- include "oan-common.labels" . -}}
{{- end }}

{{/*
Namespace for the CNPG resources. Defaults to the release namespace.
*/}}
{{- define "postgresql-cnpg.namespace" -}}
{{- default (include "oan-common.namespace" .) .Values.namespace -}}
{{- end }}

{{/*
Image reference, or empty when no image is configured - in which case the
Cluster omits imageName and the operator uses its own default image.
*/}}
{{- define "postgresql-cnpg.image" -}}
{{- if .Values.image.repository -}}
{{- include "oan-common.image" . -}}
{{- end -}}
{{- end }}

{{/*
Barman ObjectStore name (defaults to <fullname>-backup).
*/}}
{{- define "postgresql-cnpg.objectStoreName" -}}
{{- default (printf "%s-backup" (include "postgresql-cnpg.fullname" .)) .Values.backup.objectStore.name -}}
{{- end }}

{{/*
Map the object store provider to its CNPG credentials field.
*/}}
{{- define "postgresql-cnpg.objectStore.credentialsField" -}}
{{- $field := dict "s3" "s3Credentials" "gcs" "googleCredentials" "azure" "azureCredentials" -}}
{{- index $field .Values.backup.objectStore.provider -}}
{{- end }}

{{/*
Object store destination path. An explicit destinationPath wins; otherwise it is
built from the provider's URI scheme plus bucket and path.
*/}}
{{- define "postgresql-cnpg.objectStore.destinationPath" -}}
{{- $os := .Values.backup.objectStore -}}
{{- if $os.destinationPath -}}
{{- $os.destinationPath -}}
{{- else -}}
{{- $scheme := index (dict "s3" "s3://" "gcs" "gs://" "azure" "azure://") $os.provider -}}
{{- printf "%s%s%s" $scheme $os.bucket ($os.path | default "") -}}
{{- end -}}
{{- end }}

{{/*
Validate the backup configuration. Called from the templates that need it so a
misconfiguration fails the render rather than producing an ObjectStore the
plugin silently cannot use.
*/}}
{{- define "postgresql-cnpg.validateBackup" -}}
{{- $os := .Values.backup.objectStore -}}
{{- $providers := list "s3" "gcs" "azure" -}}
{{- if not (has $os.provider $providers) -}}
{{- fail (printf "%s: backup.objectStore.provider must be one of %s, got %q" .Chart.Name (join ", " $providers) $os.provider) -}}
{{- end -}}
{{- if and (not $os.bucket) (not $os.destinationPath) -}}
{{- fail (printf "%s: backup.enabled is true but neither backup.objectStore.bucket nor backup.objectStore.destinationPath is set" .Chart.Name) -}}
{{- end -}}
{{- if not $os.credentials -}}
{{- fail (printf "%s: backup.enabled is true but backup.objectStore.credentials is empty. For S3 with IRSA use {inheritFromIAMRole: true}" .Chart.Name) -}}
{{- end -}}
{{/*
Credentials keys must belong to the chosen provider. Helm merges maps, so
switching provider away from the s3 default would otherwise carry
`inheritFromIAMRole` into googleCredentials, where the plugin rejects it.
*/}}
{{- $allowed := index (dict
      "s3" (list "accessKeyId" "inheritFromIAMRole" "region" "secretAccessKey" "sessionToken")
      "gcs" (list "applicationCredentials" "gkeEnvironment")
      "azure" (list "connectionString" "inheritFromAzureAD" "storageAccount" "storageKey" "storageSasToken" "useDefaultAzureCredentials")
   ) $os.provider -}}
{{- range $key, $_ := $os.credentials -}}
{{- if not (has $key $allowed) -}}
{{- fail (printf "%s: backup.objectStore.credentials.%s is not valid for provider %q (allowed: %s). If it came from this chart's default, null it out: --set backup.objectStore.credentials.%s=null" $.Chart.Name $key $os.provider (join ", " $allowed) $key) -}}
{{- end -}}
{{- end -}}
{{- with .Values.backup.retentionPolicy -}}
{{- if not (regexMatch "^[1-9][0-9]*[dwm]$" .) -}}
{{- fail (printf "backup.retentionPolicy must match ^[1-9][0-9]*[dwm]$ (days, weeks or months, e.g. \"14d\"), got %q" .) -}}
{{- end -}}
{{- end -}}
{{- end }}
