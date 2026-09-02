{{/*
# ============================================================================
# POSTGRESQL-MIGRATION CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to oan-common, plus the Flyway target
#          and JDBC wiring this chart needs.
# ============================================================================
*/}}

{{- define "postgresql-migration.name" -}}
{{- include "oan-common.name" . -}}
{{- end }}

{{- define "postgresql-migration.fullname" -}}
{{- include "oan-common.fullname" . -}}
{{- end }}

{{- define "postgresql-migration.labels" -}}
{{- include "oan-common.labels" . -}}
{{- end }}

{{- define "postgresql-migration.selectorLabels" -}}
{{- include "oan-common.selectorLabels" . -}}
{{- end }}

{{- define "postgresql-migration.serviceAccountName" -}}
{{- include "oan-common.serviceAccount.name" . -}}
{{- end }}

{{- define "postgresql-migration.image" -}}
{{- include "oan-common.image" . -}}
{{- end }}

{{- define "postgresql-migration.envConfigMapName" -}}
{{- include "oan-common.envConfigMapName" . -}}
{{- end }}

{{/*
Targets that are enabled, in the order given. `enabled` defaults to true when
the key is absent, so a target can be written as just name + database.
*/}}
{{- define "postgresql-migration.enabledTargets" -}}
{{- $out := list -}}
{{- range .Values.targets -}}
{{- if or (not (hasKey . "enabled")) .enabled -}}
{{- $out = append $out . -}}
{{- end -}}
{{- end -}}
{{- $out | toJson -}}
{{- end }}

{{/*
Validate the connection and every target. Called from the Job and the ConfigMap
so a misconfiguration fails the render rather than the Job.
*/}}
{{- define "postgresql-migration.validate" -}}
{{- $pg := .Values.postgresql -}}
{{- if not $pg.host -}}
{{- fail (printf "%s: postgresql.host is required - point it at the PostgreSQL PRIMARY service, e.g. registry-db-rw. Migrations write, so a read-only replica will not do." .Chart.Name) -}}
{{- end -}}
{{- if not $pg.passwordSecret.name -}}
{{- fail (printf "%s: postgresql.passwordSecret.name is required - this chart renders no passwords" .Chart.Name) -}}
{{- end -}}
{{- $targets := include "postgresql-migration.enabledTargets" . | fromJsonArray -}}
{{- if not $targets -}}
{{- fail (printf "%s: no enabled entries in targets - there is nothing to migrate" .Chart.Name) -}}
{{- end -}}
{{- $seen := dict -}}
{{- range $targets -}}
{{- if not .name -}}
{{- fail (printf "%s: every entry in targets needs a name, matching a directory under files/migrations/" $.Chart.Name) -}}
{{- end -}}
{{- if not .database -}}
{{- fail (printf "%s: target %q needs a database" $.Chart.Name .name) -}}
{{- end -}}
{{- if hasKey $seen .name -}}
{{- fail (printf "%s: target %q is listed twice" $.Chart.Name .name) -}}
{{- end -}}
{{- $seen = set $seen .name true -}}
{{- $glob := printf "files/migrations/%s/*" .name -}}
{{- if not ($.Files.Glob $glob) -}}
{{- fail (printf "%s: target %q has no directory files/migrations/%s in the chart" $.Chart.Name .name .name) -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
JDBC URL for one database.
*/}}
{{- define "postgresql-migration.jdbcUrl" -}}
{{- $pg := .ctx.Values.postgresql -}}
{{- printf "jdbc:postgresql://%s:%v/%s%s" $pg.host $pg.port .database $pg.jdbcParams -}}
{{- end }}

{{/*
Name of the ConfigMap holding the migration files for one target.
*/}}
{{- define "postgresql-migration.targetConfigMapName" -}}
{{- printf "%s-%s" (include "postgresql-migration.fullname" .ctx) .name | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Where a target's migrations are mounted.
*/}}
{{- define "postgresql-migration.targetMountPath" -}}
{{- printf "/migrations/%s" .name -}}
{{- end }}

{{/*
Migration files for one target, as ConfigMap data.

Only .sql and .conf are included: the directories also hold READMEs explaining
what belongs in them, and those are not migrations.
*/}}
{{- define "postgresql-migration.targetFiles" -}}
{{- $ctx := .ctx -}}
{{- $name := .name -}}
{{- range $path, $_ := $ctx.Files.Glob (printf "files/migrations/%s/*.sql" $name) }}
{{ base $path }}: |-
  {{- $ctx.Files.Get $path | nindent 2 }}
{{- end }}
{{- range $path, $_ := $ctx.Files.Glob (printf "files/migrations/%s/*.conf" $name) }}
{{ base $path }}: |-
  {{- $ctx.Files.Get $path | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Does this target actually have migrations to apply?
*/}}
{{- define "postgresql-migration.targetHasSql" -}}
{{- if .ctx.Files.Glob (printf "files/migrations/%s/*.sql" .name) -}}
{{- true -}}
{{- end -}}
{{- end }}

{{/*
Hook annotations, when the Job runs as a Helm hook.
*/}}
{{- define "postgresql-migration.hookAnnotations" -}}
{{- if .Values.hook.enabled }}
"helm.sh/hook": {{ join "," .Values.hook.events | quote }}
"helm.sh/hook-weight": {{ .Values.hook.weight | quote }}
"helm.sh/hook-delete-policy": {{ .Values.hook.deletePolicy | quote }}
{{- end }}
{{- end }}
