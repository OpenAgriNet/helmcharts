{{/* Single mode (role network): three tiers in one adapter; config from
config/network-adapter-config.yaml, routing generated below (ADR-0001). */}}

{{/* networkAdapterDeploymentMode: single (default) | multi; only role network reads it. */}}
{{- define "adapter-service.deploymentMode" -}}
{{- $mode := .Values.networkAdapterDeploymentMode | default "single" -}}
{{- if not (has $mode (list "single" "multi")) -}}
{{- fail (printf "%s: networkAdapterDeploymentMode must be single or multi (got %q)." .Chart.Name $mode) -}}
{{- end -}}
{{- $mode -}}
{{- end }}

{{/* "true" for role network in single mode, else empty. */}}
{{- define "adapter-service.isNetworkAdapterLayer" -}}
{{- if and (eq (include "adapter-service.role" .) "network") (eq (include "adapter-service.deploymentMode" .) "single") -}}true{{- end -}}
{{- end }}

{{/* Fails unless every module's private keys are tier placeholders.
Usage: include "adapter-service.networkAdapterLayer.checkKeys" (dict "ctx" $ "cfg" $parsedConfig) */}}
{{- define "adapter-service.networkAdapterLayer.checkKeys" -}}
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

{{/* Single-mode routing as {<file>: <doc>}: tier hosts in routing-{consumer,provider}.yaml
become http://localhost:<targetPort>/_internal/<tier> (ADR-0001); routing-network.yaml as is. */}}
{{- define "adapter-service.networkAdapterLayer.routing" -}}
{{- $ctx := . -}}
{{- $ownListener := printf "http://localhost:%v/_internal/${1}${4}" .Values.service.targetPort -}}
{{- /* Anchored: only a tier host (optional namespace and port) plus optional path. */ -}}
{{- $tierHost := "^https?://(consumer|network|provider)-adapter(\\.[a-z0-9.-]+)?(:[0-9]+)?(/.*)?$" -}}
{{- $out := dict -}}
{{- range $pair := list (list "routing-consumer.yaml" "routing-network-adapter-consumer.yaml") (list "routing-network.yaml" "routing-network.yaml") (list "routing-provider.yaml" "routing-network-adapter-provider.yaml") -}}
{{- $src := index $pair 0 -}}
{{- $doc := $ctx.Files.Get (printf "config/%s" $src) | fromYaml -}}
{{- if not $doc.routingRules -}}
{{- fail (printf "%s: config/%s parsed to no routingRules; the single-mode routing is generated from it." $ctx.Chart.Name $src) -}}
{{- end -}}
{{- range $i, $rule := $doc.routingRules -}}
{{- if $rule.target -}}
{{- $url := $rule.target.url | default "" -}}
{{- $url = regexReplaceAll $tierHost $url $ownListener -}}
{{- if hasSuffix "/" $url -}}
{{- fail (printf "%s: single-mode routing from config/%s routingRules[%d] ends in a slash (%q). The router appends the action, so that produces //discover." $ctx.Chart.Name $src $i $url) -}}
{{- end -}}
{{- $_ := set $rule.target "url" $url -}}
{{- end -}}
{{- end -}}
{{- $_ := set $out (index $pair 1) $doc -}}
{{- end -}}
{{- toYaml $out -}}
{{- end }}

{{/* One tier's key Secret; fails naming the tier when unset.
Usage: include "adapter-service.networkAdapterLayer.tierSecret" (dict "ctx" $ "tier" "network") */}}
{{- define "adapter-service.networkAdapterLayer.tierSecret" -}}
{{- $name := (index .ctx.Values.networkAdapterLayer.keys .tier).existingSecret | default "" -}}
{{- if not $name -}}
{{- fail (printf "%s: networkAdapterLayer.keys.%s.existingSecret is required -- the %s tier signs as its own identity, from its own Secret (the six keys under keys.existingSecret)." .ctx.Chart.Name .tier .tier) -}}
{{- end -}}
{{- $name -}}
{{- end }}
