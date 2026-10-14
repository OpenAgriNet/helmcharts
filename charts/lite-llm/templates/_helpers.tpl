{{/*
# ============================================================================
# MODEL GATEWAY CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers delegating to common, plus the datastore, secret
#          and reconciliation wiring this gateway needs.
# ============================================================================
*/}}

{{- define "model-gateway.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "model-gateway.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "model-gateway.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "model-gateway.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{- define "model-gateway.serviceAccountName" -}}
{{- include "common.serviceAccount.name" . -}}
{{- end }}

{{- define "model-gateway.image" -}}
{{- include "common.image" . -}}
{{- end }}

{{- define "model-gateway.envConfigMapName" -}}
{{- include "common.envConfigMapName" . -}}
{{- end }}

{{/*
The in-cluster address of the gateway's own API. The reconcile Job talks to it
here, so it never needs an Ingress and the admin API is never published.
*/}}
{{- define "model-gateway.internalURL" -}}
{{- printf "http://%s:%v" (include "model-gateway.fullname" .) .Values.service.port -}}
{{- end }}

{{- define "model-gateway.modelsConfigMapName" -}}
{{- printf "%s-models" (include "model-gateway.fullname" .) -}}
{{- end }}

{{- define "model-gateway.reconcileConfigMapName" -}}
{{- printf "%s-reconcile" (include "model-gateway.fullname" .) -}}
{{- end }}

{{/*
The desired model list, as the reconcile Job reads it.

Rendered from values rather than pasted in as a document, so a model is a value
key like everything else: reviewable in a diff, overridable per environment, and
checked below before it reaches the cluster.
*/}}
{{- define "model-gateway.modelsJSON" -}}
{{- $models := .Values.models -}}
{{- if not $models }}
{{- fail (printf "%s: models is empty. The assistant asks for names this chart has not defined, and every turn would fail on an unknown model." .Chart.Name) }}
{{- end }}
{{- range $models }}
{{- if not .name }}
{{- fail (printf "%s: every entry in models needs a name - it is what the assistant asks for, e.g. dss-composer" $.Chart.Name) }}
{{- end }}
{{- if not .model }}
{{- fail (printf "%s: model %q has no model - set the vendor and model, e.g. openai/<azure-deployment> or anthropic/<id>" $.Chart.Name .name) }}
{{- end }}
{{- if not .credential }}
{{- fail (printf "%s: model %q has no credential - name a vendor account from `credentials`, so no key is ever written into this chart" $.Chart.Name .name) }}
{{- end }}
{{- if or (not .inputCostPerToken) (not .outputCostPerToken) }}
{{- fail (printf "%s: model %q has no price. A model the gateway cannot price is recorded as costing nothing, and a spend limit over nothing never stops anything." $.Chart.Name .name) }}
{{- end }}
{{- end }}
{{- toJson $models -}}
{{- end }}

{{/*
The vendor accounts, as the reconcile Job reads them: a name, and where to read
the key from. Never a key. This repository renders no Secrets and holds no
secret values.
*/}}
{{- define "model-gateway.credentialsJSON" -}}
{{- range .Values.credentials }}
{{- if not .secretName }}
{{- fail (printf "%s: credential %q has no secretName - the key is read from a Secret that exists in the cluster already" $.Chart.Name .name) }}
{{- end }}
{{- end }}
{{- toJson .Values.credentials -}}
{{- end }}

{{/*
Everything the gateway needs that this chart derives from its structured values.
Guardrails refuse to render what the gateway would refuse to run on, or - worse
- would run on while quietly doing the wrong thing.
*/}}
{{- define "model-gateway.env" -}}
{{- $db := .Values.database -}}
{{- if not $db.urlSecret.name }}
{{- fail (printf "%s: database.urlSecret.name is required. The gateway keeps its keys, spend and model list in PostgreSQL; CNPG writes a ready-made `uri` key into Secret/<cluster>-app." .Chart.Name) }}
{{- end }}
{{- if not .Values.masterKeySecret.name }}
{{- fail (printf "%s: masterKeySecret.name is required - it is the gateway's own admin credential" .Chart.Name) }}
{{- end }}
{{- if not .Values.saltKeySecret.name }}
{{- fail (printf "%s: saltKeySecret.name is required. It encrypts every vendor key in the database. Create it ONCE, keep it with the other secrets, and never rotate it: change it and every stored key becomes unreadable, the gateway sends nonsense to the vendor, and the vendor reports an invalid key with nothing else to go on." .Chart.Name) }}
{{- end }}
{{- if and (gt (int .Values.replicaCount) 1) (not .Values.redis.host) }}
{{- fail (printf "%s: replicaCount is %v and redis.host is unset. Spend and rate limits are counted per process without Redis, so two replicas enforce roughly twice the limit that was asked for - the limits would look configured and not hold." .Chart.Name (int .Values.replicaCount)) }}
{{- end }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ $db.urlSecret.name }}
      key: {{ $db.urlSecret.key }}
- name: LITELLM_MASTER_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.masterKeySecret.name }}
      key: {{ .Values.masterKeySecret.key }}
- name: LITELLM_SALT_KEY
  valueFrom:
    secretKeyRef:
      name: {{ .Values.saltKeySecret.name }}
      key: {{ .Values.saltKeySecret.key }}
{{- /* The model list is this chart's, applied through the API by the reconcile
       Job. Storing it in the database is what lets a model change take effect
       without restarting the gateway. */}}
- name: STORE_MODEL_IN_DB
  value: "True"
{{- /* Off by default. The team keeps this configuration in version control, and
       a screen that edits the running gateway is a second source of truth that
       nothing reviews. */}}
- name: DISABLE_ADMIN_UI
  value: {{ .Values.adminUI.enabled | not | quote | title }}
{{- with .Values.redis.host }}
- name: REDIS_HOST
  value: {{ . | quote }}
- name: REDIS_PORT
  value: {{ $.Values.redis.port | quote }}
{{- with $.Values.redis.passwordSecret.name }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: {{ $.Values.redis.passwordSecret.key }}
{{- end }}
{{- end }}
{{- if .Values.otel.enabled }}
{{- if not .Values.otel.endpoint }}
{{- fail (printf "%s: otel.enabled is true, so otel.endpoint is required - point it at the collector that filters this gateway's internal spans, e.g. http://model-gateway-collector:4318/v1/traces" .Chart.Name) }}
{{- end }}
- name: OTEL_EXPORTER
  value: {{ .Values.otel.exporter | quote }}
- name: OTEL_ENDPOINT
  value: {{ .Values.otel.endpoint | quote }}
- name: OTEL_SERVICE_NAME
  value: {{ include "model-gateway.fullname" . | quote }}
{{- end }}
{{- with (include "common.env" . | trim) }}
{{ . }}
{{- end }}
{{- end }}
