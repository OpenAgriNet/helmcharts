{{/* Single mode (role network-adapter): three tiers in one adapter; config from
config/single-adapter/network-adapter-config.yaml and its routing files (ADR-0001). */}}

{{/* deploymentMode (single-adapter | multi-adapter); provider and consumer are always multi-adapter. */}}
{{- define "adapter-service.deploymentMode" -}}
{{- $mode := .Values.deploymentMode | default "single-adapter" -}}
{{- if not (has $mode (list "single-adapter" "multi-adapter")) -}}
{{- fail (printf "%s: deploymentMode must be single-adapter or multi-adapter (got %q)." .Chart.Name $mode) -}}
{{- end -}}
{{- ternary $mode "multi-adapter" (eq (include "adapter-service.role" .) "network") -}}
{{- end }}

{{/* Fails unless every module's private keys are tier placeholders.
Usage: include "adapter-service.networkCheckTierKeys" (dict "ctx" $ "cfg" $parsedConfig) */}}
{{- define "adapter-service.networkCheckTierKeys" -}}
{{- range $m := .cfg.modules -}}
{{- $km := dig "handler" "plugins" "keyManager" "config" dict $m -}}
{{- range $field := list "signingPrivateKey" "encrPrivateKey" -}}
{{- $v := index $km $field | default "" | toString -}}
{{- if and $v (not (regexMatch "^__(CONSUMER|NETWORK|PROVIDER)_(SIGNING|ENCR)_PRIVATE__$" $v)) -}}
{{- fail (printf "%s: single-mode module %s sets %s to a value that is not a tier placeholder. A private key written into config reaches git, the ConfigMap and `helm get values`; use __<TIER>_%s__ and the init container fills it from that tier's Secret." $.ctx.Chart.Name $m.name $field (ternary "SIGNING_PRIVATE" "ENCR_PRIVATE" (eq $field "signingPrivateKey"))) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/* One tier's key Secret; fails naming the tier when unset.
Usage: include "adapter-service.networkTierSecret" (dict "ctx" $ "tier" "network") */}}
{{- define "adapter-service.networkTierSecret" -}}
{{- $name := index .ctx.Values.adapterKeys (printf "%s-adapter" .tier) | default "" -}}
{{- if not $name -}}
{{- fail (printf "%s: adapterKeys.%s-adapter is required -- the %s tier signs as its own identity, from its own Secret (the six keys under keys.existingSecret)." .ctx.Chart.Name .tier .tier) -}}
{{- end -}}
{{- $name -}}
{{- end }}
