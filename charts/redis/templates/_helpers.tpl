{{/*
# ============================================================================
# REDIS CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "redis.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "redis.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "redis.chart" -}}
{{- include "common.chart" . -}}
{{- end }}

{{- define "redis.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "redis.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "redis.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "redis.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{/*
REDIS_PASSWORD, from an existing Secret - this chart renders no Secret. There
is deliberately no "auth off" switch: an empty auth.existingSecret fails the
render rather than start a Redis that anything in the cluster can read form
values out of. Appends common.env (secretEnv / extraEnv).
*/}}
{{- define "redis.env" -}}
{{- $auth := .Values.auth -}}
{{- if not $auth.existingSecret }}
{{- fail (printf "%s: auth.existingSecret is required - this chart renders no Secrets and never runs Redis without a password. Create one first, e.g. kubectl -n %s create secret generic redis --from-literal=password=<pw>, then set auth.existingSecret=redis" .Chart.Name .Release.Namespace) }}
{{- end }}
{{- if not $auth.passwordKey }}
{{- fail (printf "%s: auth.passwordKey is required - it names the key inside Secret %q that holds the password" .Chart.Name $auth.existingSecret) }}
{{- end }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $auth.existingSecret }}
      key: {{ $auth.passwordKey }}
{{- /* redis-cli reads REDISCLI_AUTH by itself, so the probes and anyone who
       execs in can run a plain `redis-cli ping` - the password never goes
       on a command line, where /proc/<pid>/cmdline and `ps` would show it. */}}
- name: REDISCLI_AUTH
  valueFrom:
    secretKeyRef:
      name: {{ $auth.existingSecret }}
      key: {{ $auth.passwordKey }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}

{{/*
The redis-server command line, run through `sh -c`.

The password goes in as config on stdin (`redis-server -` reads a config
file from stdin; the flags after it are appended), through a here-document,
so it is never on a command line: not in the pod spec, and not in
/proc/1/cmdline or `ps` on the node. `exec` makes redis-server PID 1, so it
receives SIGTERM directly and shuts down cleanly.

--save "" and --appendonly no turn off both RDB and AOF: nothing lands on
disk, which is the point (ADR-0022).
*/}}
{{- define "redis.serverCommand" -}}
{{- printf "exec redis-server - --save \"\" --appendonly no --maxmemory %s --maxmemory-policy %s <<EOF\nrequirepass \"$REDIS_PASSWORD\"\nEOF" (required "maxmemory is required" .Values.maxmemory | toString) (required "maxmemoryPolicy is required" .Values.maxmemoryPolicy) -}}
{{- end }}
