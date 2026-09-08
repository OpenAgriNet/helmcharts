{{/*
# ============================================================================
# NETWORK ADAPTER CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to oan-common, plus the identity,
#          upstream and config-rendering wiring this adapter needs.
# ============================================================================
*/}}

{{- define "network-adapter.name" -}}
{{- include "oan-common.name" . -}}
{{- end }}

{{- define "network-adapter.fullname" -}}
{{- include "oan-common.fullname" . -}}
{{- end }}

{{- define "network-adapter.labels" -}}
{{- include "oan-common.labels" . -}}
{{- end }}

{{- define "network-adapter.selectorLabels" -}}
{{- include "oan-common.selectorLabels" . -}}
{{- end }}

{{- define "network-adapter.serviceAccountName" -}}
{{- include "oan-common.serviceAccount.name" . -}}
{{- end }}

{{- define "network-adapter.image" -}}
{{- include "oan-common.image" . -}}
{{- end }}

{{/*
The Secret holding this adapter's keypair.

Failing here rather than letting the render succeed is the point. Without it
the config would carry the literal placeholders through to the process, which
starts, serves /health, reports Ready, and then fails every signature it
attempts -- at the far end, in a peer's logs, with nothing in this pod saying
why.
*/}}
{{- define "network-adapter.keysSecretName" -}}
{{- $name := .Values.keys.existingSecret.name | default "" -}}
{{- if not $name -}}
{{- fail (printf "%s: keys.existingSecret.name is required. This adapter's identity is a keypair, this chart renders no Secrets, and a pod without one starts and reports Ready while failing every signature it makes. See values.yaml for the six keys it must hold." .Chart.Name) -}}
{{- end -}}
{{- $name -}}
{{- end }}

{{/*
The registry API base. Required for the same reason as the keys: absent, the
adapter builds a plugin pointed at nothing and every signature verification
fails on a lookup rather than on the signature.
*/}}
{{- define "network-adapter.registryUrl" -}}
{{- $url := .Values.registry.url | default "" -}}
{{- if not $url -}}
{{- fail (printf "%s: registry.url is required -- e.g. http://registry:8081/api/v1. The adapter verifies every caller against the key the registry publishes for them, so with no registry it can verify nobody." .Chart.Name) -}}
{{- end -}}
{{- $url -}}
{{- end }}

{{/*
Where discover and publish go. No trailing path: targetType "url" appends the
action, so anything here becomes a prefix on every forwarded call.
*/}}
{{- define "network-adapter.discoveryUrl" -}}
{{- $url := .Values.discovery.url | default "" -}}
{{- if not $url -}}
{{- fail (printf "%s: discovery.url is required -- e.g. http://discovery:8080. This adapter forwards discover and publish there and answers neither itself, so with it unset every request it accepts has nowhere to go." .Chart.Name) -}}
{{- end -}}
{{- if hasSuffix "/" $url -}}
{{- fail (printf "%s: discovery.url must not end in a slash (%q). The router appends the action to it, so a trailing slash produces //discover." .Chart.Name $url) -}}
{{- end -}}
{{- $url -}}
{{- end }}

{{/*
OTLP endpoint, required only when telemetry is on. An enabled exporter with
no endpoint dials nothing and logs a failure per export interval, which is
the noise the three enable flags exist to avoid.
*/}}
{{- define "network-adapter.otlpEndpoint" -}}
{{- if .Values.otel.enabled -}}
{{- $ep := .Values.otel.endpoint | default "" -}}
{{- if not $ep -}}
{{- fail (printf "%s: otel.endpoint is required when otel.enabled is true -- e.g. otel-collector.observability.svc.cluster.local:4317. An enabled exporter with nowhere to send logs a failure every export interval." .Chart.Name) -}}
{{- end -}}
{{- $ep -}}
{{- end -}}
{{- end }}

{{/*
Where the rendered config lands. An emptyDir, because the init container
writes it and readOnlyRootFilesystem means there is nowhere else to write.
*/}}
{{- define "network-adapter.configDir" -}}
/app/config
{{- end }}

{{/*
Annotation that rolls the pods when the config changes.

The keys are not in this checksum and must not be: the Secret is not rendered
by this chart, so its content is not knowable at template time. Rotating a key
therefore needs a rollout restart -- which the NOTES print.
*/}}
{{- define "network-adapter.configChecksum" -}}
{{- include (print $.Template.BasePath "/configmap.yaml") . | sha256sum -}}
{{- end }}
