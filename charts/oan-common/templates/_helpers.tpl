{{/*
# ============================================================================
# OAN COMMON LIBRARY CHART - SHARED HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: reusable template helpers consumed by every OAN service chart.
#          This chart renders no resources of its own.
# GitHub: https://github.com/OpenAgriNet/helmcharts/blob/main/charts/oan-common/templates/_helpers.tpl
# ============================================================================
*/}}

{{/*
Chart name, honoring nameOverride.
Usage: {{ include "oan-common.name" . }}
*/}}
{{- define "oan-common.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name (<release>-<chart>), honoring fullnameOverride.
Truncated to 63 chars for the DNS label limit.
Usage: {{ include "oan-common.fullname" . }}
*/}}
{{- define "oan-common.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name and version for the helm.sh/chart label.
Usage: {{ include "oan-common.chart" . }}
*/}}
{{- define "oan-common.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Standard labels for every resource.
Usage: {{ include "oan-common.labels" . | nindent 4 }}
*/}}
{{- define "oan-common.labels" -}}
helm.sh/chart: {{ include "oan-common.chart" . }}
{{ include "oan-common.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: oan
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels for pods and services. These are immutable on a Deployment
selector, so nothing environment-specific belongs here.
Usage: {{ include "oan-common.selectorLabels" . | nindent 4 }}
*/}}
{{- define "oan-common.selectorLabels" -}}
app.kubernetes.io/name: {{ include "oan-common.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Common annotations for every resource.
Usage: {{ include "oan-common.annotations" . | nindent 4 }}
*/}}
{{- define "oan-common.annotations" -}}
{{- with .Values.commonAnnotations }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Name of the service account to use.
Usage: {{ include "oan-common.serviceAccount.name" . }}
*/}}
{{- define "oan-common.serviceAccount.name" -}}
{{- if .Values.serviceAccount.enabled }}
{{- default (include "oan-common.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Emits "true" when a ServiceAccount should be created.
Usage: {{ if include "oan-common.serviceAccount.enabled" . }}
*/}}
{{- define "oan-common.serviceAccount.enabled" -}}
{{- if .Values.serviceAccount.enabled }}
{{- true }}
{{- end }}
{{- end }}

{{/*
Full image reference: <registry>/<repository>:<tag>, or
<registry>/<repository>@<digest> when image.digest is set.

A digest wins over a tag, so an environment can pin an exact image without
having to blank out the tag.

Tag falls back to Chart.appVersion, then "latest".
Usage: {{ include "oan-common.image" . }}
*/}}
{{- define "oan-common.image" -}}
{{- $registry := .Values.image.registry | default "" }}
{{- $repository := .Values.image.repository | default "" }}
{{- if not $repository }}
{{/*
Without this, an empty repository renders a syntactically valid but meaningless
reference - "ghcr.io/:v2.0.0" - which Helm and the API server both accept. The
failure only surfaces later as an ImagePullBackOff, long after the deploy looked
successful. Fail here instead.
*/}}
{{- fail (printf "%s: image.repository is required - set image.registry/repository/tag for this environment" .Chart.Name) }}
{{- end }}
{{- if $registry }}
{{- $repository = printf "%s/%s" $registry $repository }}
{{- end }}
{{- with .Values.image.digest }}
{{- printf "%s@%s" $repository . }}
{{- else }}
{{- printf "%s:%s" $repository (.Values.image.tag | default $.Chart.AppVersion | default "latest") }}
{{- end }}
{{- end }}

{{/*
imagePullSecrets block, rendered only when image.pullSecrets is non-empty.
Usage: {{ include "oan-common.imagePullSecrets" . | nindent 6 }}
*/}}
{{- define "oan-common.imagePullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Name of the env ConfigMap (<fullname>-env).
Usage: {{ include "oan-common.envConfigMapName" . }}
*/}}
{{- define "oan-common.envConfigMapName" -}}
{{- printf "%s-env" (include "oan-common.fullname" .) }}
{{- end }}

{{/*
envConfig rendered as ConfigMap data entries.
Usage: {{ include "oan-common.envConfigMapData" . | nindent 2 }}
*/}}
{{- define "oan-common.envConfigMapData" -}}
{{- range $key, $value := .Values.envConfig }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- end }}

{{/*
Checksum of envConfig, so a config change rolls the pods.
Usage: checksum/env-config: {{ include "oan-common.checksumAnnotation" . }}
*/}}
{{- define "oan-common.checksumAnnotation" -}}
{{- $envConfig := .Values.envConfig | default dict }}
{{- $envConfig | toJson | sha256sum }}
{{- end }}

{{/*
Container env entries built from `secretEnv` and `extraEnv`.

`secretEnv` maps an environment variable name to a secret key, for the common
case where the producing secret's key name differs from the variable the app
expects (CNPG writes `password`; Sunbird RC wants `connectionInfo_password`):

  secretEnv:
    connectionInfo_password:
      name: registry-db-app
      key: password
      optional: false      # optional

`extraEnv` is a raw list of env entries, passed through verbatim for anything
this schema does not cover (fieldRef, resourceFieldRef, plain values).

Usage:
  {{- with (include "oan-common.env" . | trim) }}
  env:
    {{- . | nindent 12 }}
  {{- end }}
*/}}
{{- define "oan-common.env" -}}
{{- range $name, $ref := .Values.secretEnv }}
{{- if not $ref.name }}
{{- fail (printf "secretEnv.%s.name is required - it must name the Secret holding the value" $name) }}
{{- end }}
{{- if not $ref.key }}
{{- fail (printf "secretEnv.%s.key is required - it must name the key inside Secret %q" $name $ref.name) }}
{{- end }}
- name: {{ $name }}
  valueFrom:
    secretKeyRef:
      name: {{ $ref.name }}
      key: {{ $ref.key }}
      {{- if hasKey $ref "optional" }}
      optional: {{ $ref.optional }}
      {{- end }}
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Init containers that block startup until dependencies are reachable.

Kubernetes has no equivalent of compose's `depends_on: condition:
service_healthy`. Without this a pod starts before its database or its identity
provider is up, fails, and crashloops with backoff - which recovers on its own
but makes a first install look broken and slows every restart.

Takes the checks explicitly, so a consuming chart derives them from its own
settings (database.host, keycloak.url) instead of making the operator retype
values that would then be free to drift:

  {{- include "oan-common.waitFor" (dict "ctx" . "tcp" $tcp "http" $http) }}

where $tcp entries are {name, host, port} and $http entries are {name, url}.

TCP checks use `nc -z`; HTTP checks use `wget --spider`. Both loop until success
or waitFor.timeoutSeconds, then fail the pod so the reason is visible in
`kubectl describe` rather than buried in a crashloop.
*/}}
{{- define "oan-common.waitFor" -}}
{{- $ctx := .ctx -}}
{{- $w := $ctx.Values.waitFor | default dict -}}
{{- if $w.enabled -}}
{{- $img := $w.image | default dict -}}
{{- $image := printf "%s/%s:%s" ($img.registry | default "docker.io") ($img.repository | default "busybox") ($img.tag | default "latest") -}}
{{- $timeout := $w.timeoutSeconds | default 300 -}}
{{- $interval := $w.intervalSeconds | default 3 -}}
{{- $res := $w.resources | default dict -}}
{{- range .tcp }}
{{- if not .host }}
{{- fail (printf "waitFor: the %q check has no host. Set the setting it derives from (e.g. database.host), or disable the check." (.name | default "<unnamed>")) }}
{{- end }}
- name: {{ printf "wait-%s" (.name | default "tcp") | trunc 63 | trimSuffix "-" }}
  image: {{ $image | quote }}
  imagePullPolicy: {{ $img.pullPolicy | default "IfNotPresent" }}
  command:
    - /bin/sh
    - -c
    - |
      deadline=$(( $(date +%s) + {{ $timeout }} ))
      until nc -z {{ .host | quote }} {{ .port }}; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
          echo "timed out after {{ $timeout }}s waiting for {{ .name }} at {{ .host }}:{{ .port }}"
          exit 1
        fi
        echo "waiting for {{ .name }} at {{ .host }}:{{ .port }}"
        sleep {{ $interval }}
      done
      echo "{{ .name }} is reachable"
  resources:
    {{- toYaml $res | nindent 4 }}
{{- end }}
{{- range .http }}
{{- if not .url }}
{{- fail (printf "waitFor: the %q check has no url. Set the setting it derives from (e.g. keycloak.url), or disable the check." (.name | default "<unnamed>")) }}
{{- end }}
- name: {{ printf "wait-%s" (.name | default "http") | trunc 63 | trimSuffix "-" }}
  image: {{ $image | quote }}
  imagePullPolicy: {{ $img.pullPolicy | default "IfNotPresent" }}
  command:
    - /bin/sh
    - -c
    - |
      deadline=$(( $(date +%s) + {{ $timeout }} ))
      until wget -q --spider --timeout=5 {{ .url | quote }}; do
        if [ "$(date +%s)" -ge "$deadline" ]; then
          echo "timed out after {{ $timeout }}s waiting for {{ .name }} at {{ .url }}"
          exit 1
        fi
        echo "waiting for {{ .name }} at {{ .url }}"
        sleep {{ $interval }}
      done
      echo "{{ .name }} is reachable"
  resources:
    {{- toYaml $res | nindent 4 }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Resources block.
Fails the render when resources is empty: every OAN component must declare a
resource contract so the scheduler and the cluster autoscaler have real numbers
to work with.
Usage: {{ include "oan-common.resources" . | nindent 10 }}
*/}}
{{- define "oan-common.resources" -}}
{{- if not .Values.resources -}}
{{- fail (printf "%s: .Values.resources is required - every OAN component must declare requests and limits (see CONVENTIONS.md)" .Chart.Name) -}}
{{- end -}}
{{- toYaml .Values.resources }}
{{- end }}

{{/*
Render one probe. Everything except the `enabled` flag is passed through
verbatim, so any handler (httpGet, tcpSocket, exec, grpc) and any timing field
works.

Guardrail: Helm merges maps, so overriding a chart's default httpGet probe with
tcpSocket would leave BOTH handlers in the merged value - which the API server
rejects at apply time, long after the render looked fine. This fails the render
instead and tells you to null out the default.

Usage: {{ include "oan-common.probeSpec"
          (dict "probe" .Values.livenessProbe "name" "livenessProbe" "chart" .Chart.Name) | nindent 2 }}
*/}}
{{- define "oan-common.probeSpec" -}}
{{- $probe := .probe -}}
{{- $name := .name | default "probe" -}}
{{- $chart := .chart | default "chart" -}}
{{- $handlers := list -}}
{{- range $h := list "httpGet" "tcpSocket" "exec" "grpc" -}}
{{- if index $probe $h -}}
{{- $handlers = append $handlers $h -}}
{{- end -}}
{{- end -}}
{{- if gt (len $handlers) 1 -}}
{{- fail (printf "%s: %s declares %d handlers (%s) but a probe may declare only one. Null out the one you do not want, e.g. --set %s.httpGet=null" $chart $name (len $handlers) (join ", " $handlers) $name) -}}
{{- end -}}
{{- if eq (len $handlers) 0 -}}
{{- fail (printf "%s: %s is enabled but declares no handler. Set one of httpGet, tcpSocket, exec or grpc." $chart $name) -}}
{{- end -}}
{{- toYaml (omit $probe "enabled") }}
{{- end }}

{{/*
All enabled probe blocks (startup, liveness, readiness) for a container spec.
Usage: {{ include "oan-common.probes" . | nindent 8 }}
*/}}
{{- define "oan-common.probes" -}}
{{- if and .Values.startupProbe .Values.startupProbe.enabled }}
startupProbe:
  {{- include "oan-common.probeSpec" (dict "probe" .Values.startupProbe "name" "startupProbe" "chart" .Chart.Name) | nindent 2 }}
{{- end }}
{{- if and .Values.livenessProbe .Values.livenessProbe.enabled }}
livenessProbe:
  {{- include "oan-common.probeSpec" (dict "probe" .Values.livenessProbe "name" "livenessProbe" "chart" .Chart.Name) | nindent 2 }}
{{- end }}
{{- if and .Values.readinessProbe .Values.readinessProbe.enabled }}
readinessProbe:
  {{- include "oan-common.probeSpec" (dict "probe" .Values.readinessProbe "name" "readinessProbe" "chart" .Chart.Name) | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Pod-level security context, rendered only when enabled.
Usage: {{ include "oan-common.podSecurityContext" . | nindent 8 }}
*/}}
{{- define "oan-common.podSecurityContext" -}}
{{- if and .Values.podSecurityContext .Values.podSecurityContext.enabled }}
{{- toYaml (omit .Values.podSecurityContext "enabled") }}
{{- end }}
{{- end }}

{{/*
Container-level security context, rendered only when enabled.
Usage: {{ include "oan-common.securityContext" . | nindent 10 }}
*/}}
{{- define "oan-common.securityContext" -}}
{{- if and .Values.securityContext .Values.securityContext.enabled }}
{{- toYaml (omit .Values.securityContext "enabled") }}
{{- end }}
{{- end }}

{{/*
Release namespace.
Usage: {{ include "oan-common.namespace" . }}
*/}}
{{- define "oan-common.namespace" -}}
{{- .Release.Namespace }}
{{- end }}

{{/*
apiVersion helpers, so a bump lands in one place.
*/}}
{{- define "oan-common.deployment.apiVersion" -}}
apps/v1
{{- end }}

{{- define "oan-common.ingress.apiVersion" -}}
networking.k8s.io/v1
{{- end }}
