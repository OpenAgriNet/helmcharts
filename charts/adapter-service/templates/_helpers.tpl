{{/*
# ============================================================================
# ADAPTER SERVICE CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to common, plus the role,
#          identity, upstream and config-rendering wiring an adapter needs.
#
# One chart, four roles. provider, network and consumer (one release per tier)
# and network-adapter-layer (all three tiers in one adapter) are the same image and the
# same config format; the role decides the handler role, the step list and
# where requests are routed. Install once per role, and keep the RELEASE name
# per-role -- the release name is what becomes the Service DNS name that the
# other adapters address.
# ============================================================================
*/}}

{{/*
The adapter's role: provider | network | consumer | network-adapter-layer.

Required, with no default. A default would silently give one role's step list
and handler role to a different adapter -- which renders, starts, reports Ready
and then mis-handles every request, at the far end, in a peer's logs.
*/}}
{{- define "adapter-service.role" -}}
{{- $role := .Values.role | default "" -}}
{{- if not $role -}}
{{- fail (printf "%s: role is required -- one of provider, network, consumer (the three multi-adapter tiers) or network-adapter-layer (all three tiers in one adapter). It decides the handler role, the step list and the routing target, so there is no safe default. See examples/ for a values file per role." .Chart.Name) -}}
{{- end -}}
{{- if not (has $role (list "provider" "network" "consumer" "network-adapter-layer")) -}}
{{- fail (printf "%s: role must be one of provider, network, consumer, network-adapter-layer (got %q)." .Chart.Name $role) -}}
{{- end -}}
{{- $role -}}
{{- end }}

{{/*
bap originates a call, bpp receives one. consumer is the caller; network and
provider receive. Overridable, because the role is a shorthand for a default
rather than a constraint.
*/}}
{{- define "adapter-service.handlerRole" -}}
{{- if .Values.handler.role -}}
{{- .Values.handler.role -}}
{{- else if eq (include "adapter-service.role" .) "consumer" -}}
bap
{{- else -}}
bpp
{{- end -}}
{{- end }}

{{/*
The step list, as a YAML array.

consumer sits inside the trust boundary and accepts unsigned requests, so it has no
signature to validate; the other two receive from the network and must verify
first. Set handler.steps to override.
*/}}
{{- define "adapter-service.steps" -}}
{{- if .Values.handler.steps -}}
{{- toYaml .Values.handler.steps -}}
{{- else if eq (include "adapter-service.role" .) "consumer" -}}
{{- toYaml (list "addRoute" "sign") -}}
{{- else -}}
{{- toYaml (list "validateSign" "addRoute" "sign") -}}
{{- end -}}
{{- end }}

{{/*
The routing config filename. One file per role so a rendered config is
self-describing when you exec into a pod.
*/}}
{{- define "adapter-service.routingFile" -}}
routing-{{ include "adapter-service.role" . }}.yaml
{{- end }}

{{- define "adapter-service.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "adapter-service.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "adapter-service.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "adapter-service.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "adapter-service.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{/*
The Secret holding this adapter's keypair.

Failing here rather than letting the render succeed is the point. Without it
the config would carry the literal placeholders through to the process, which
starts, serves /health, reports Ready, and then fails every signature it
attempts -- at the far end, in a peer's logs, with nothing in this pod saying
why.
*/}}
{{- define "adapter-service.keysSecretName" -}}
{{- $name := .Values.keys.existingSecret.name | default "" -}}
{{- if not $name -}}
{{- fail (printf "%s: keys.existingSecret.name is required. This adapter's identity is a keypair, this chart renders no Secrets, and a pod without one starts and reports Ready while failing every signature it makes. See values.yaml for the six keys it must hold." .Chart.Name) -}}
{{- end -}}
{{- $name -}}
{{- end }}

{{/*
Where the rendered config lands. An emptyDir, because the init container
writes it and readOnlyRootFilesystem means there is nowhere else to write.
*/}}
{{- define "adapter-service.configDir" -}}
/app/config
{{- end }}

{{/*
Annotation that rolls the pods when the config changes.

The keys are not in this checksum and must not be: the Secret is not rendered
by this chart, so its content is not knowable at template time. Rotating a key
therefore needs a rollout restart -- which the NOTES print.
*/}}
{{- define "adapter-service.configChecksum" -}}
{{- include (print $.Template.BasePath "/configmap.yaml") . | sha256sum -}}
{{- end }}
