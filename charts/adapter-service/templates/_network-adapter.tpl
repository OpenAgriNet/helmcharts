{{/*
# ============================================================================
# role: network-adapter-layer -- the Network Adapter Layer.
#
# The consumer, network and provider tiers as four modules of ONE adapter
# process. The adapter config is config/network-adapter-config.yaml
# (configmap.yaml mounts it like any role's file). The routing is GENERATED at
# render time from config/routing-<tier>.yaml, the same files the three
# multi-adapter roles mount, with tier hosts pointed at this pod's own listener.
# ============================================================================
*/}}

{{/*
"true" when this release runs the Network Adapter Layer, else empty.
*/}}
{{- define "adapter-service.isNetworkAdapterLayer" -}}
{{- if eq (include "adapter-service.role" .) "network-adapter-layer" -}}true{{- end -}}
{{- end }}

{{/*
Fails unless every private-key VALUE in a network-adapter-layer config is one of the
tier placeholders. Parsed, not grepped: it reads each module's keyManager.
Usage: include "adapter-service.networkAdapterLayer.checkKeys" (dict "ctx" $ "cfg" $parsedConfig)
*/}}
{{- define "adapter-service.networkAdapterLayer.checkKeys" -}}
{{- range $m := .cfg.modules -}}
{{- $km := dig "handler" "plugins" "keyManager" "config" dict $m -}}
{{- range $field := list "signingPrivateKey" "encrPrivateKey" -}}
{{- $v := index $km $field | default "" | toString -}}
{{- if and $v (not (regexMatch "^__(CONSUMER|NETWORK|PROVIDER)_(SIGNING|ENCR)_PRIVATE__$" $v)) -}}
{{- fail (printf "%s: network-adapter-layer module %s sets %s to a value that is not a tier placeholder. A private key written into config reaches git, the ConfigMap and `helm get values`; use __<TIER>_%s__ and the init container fills it from that tier's Secret." $.ctx.Chart.Name $m.name $field (ternary "SIGNING_PRIVATE" "ENCR_PRIVATE" (eq $field "signingPrivateKey"))) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
Routing for the Network Adapter Layer, as YAML: {<file name>: <routing doc>}.

routing-network.yaml is the multi-adapter file, unchanged (discover and publish
to discovery). The other two are generated from routing-consumer.yaml and
routing-provider.yaml by pointing every tier address at THIS adapter's own
listener over loopback HTTP: http://<tier>-adapter[.<ns>...][:port] becomes
http://localhost:<targetPort>/<tier>, and any path after it is kept
(.../publish becomes /network/publish). A target that is not a tier adapter is
kept as it is, and a rule with no target is left alone (the same nil guard as
configmap.yaml). So the two modes are one routing source, and splitting the
modules across pods again changes only the hosts.
*/}}
{{- define "adapter-service.networkAdapterLayer.routing" -}}
{{- $ctx := . -}}
{{- $loopback := printf "http://localhost:%v/${1}${4}" .Values.service.targetPort -}}
{{- /* Whole URL, anchored both ends: only a tier host (optionally with a
       namespace suffix and port) followed by nothing or a path is rewritten. */ -}}
{{- $tierHost := "^https?://(consumer|network|provider)-adapter(\\.[a-z0-9.-]+)?(:[0-9]+)?(/.*)?$" -}}
{{- $out := dict -}}
{{- range $pair := list (list "routing-consumer.yaml" "routing-network-adapter-consumer.yaml") (list "routing-network.yaml" "routing-network.yaml") (list "routing-provider.yaml" "routing-network-adapter-publish.yaml") -}}
{{- $src := index $pair 0 -}}
{{- $doc := $ctx.Files.Get (printf "config/%s" $src) | fromYaml -}}
{{- if not $doc.routingRules -}}
{{- fail (printf "%s: config/%s parsed to no routingRules; the network-adapter-layer routing is generated from it." $ctx.Chart.Name $src) -}}
{{- end -}}
{{- range $i, $rule := $doc.routingRules -}}
{{- if $rule.target -}}
{{- $url := $rule.target.url | default "" -}}
{{- $url = regexReplaceAll $tierHost $url $loopback -}}
{{- if hasSuffix "/" $url -}}
{{- fail (printf "%s: network-adapter-layer routing from config/%s routingRules[%d] ends in a slash (%q). The router appends the action, so that produces //discover." $ctx.Chart.Name $src $i $url) -}}
{{- end -}}
{{- $_ := set $rule.target "url" $url -}}
{{- end -}}
{{- end -}}
{{- $_ := set $out (index $pair 1) $doc -}}
{{- end -}}
{{- toYaml $out -}}
{{- end }}

{{/*
Secret holding one tier's keypair. Fails naming the tier: a missing identity
would otherwise start, report Ready, and fail every signature that tier makes.
Usage: include "adapter-service.networkAdapterLayer.tierSecret" (dict "ctx" $ "tier" "network")
*/}}
{{- define "adapter-service.networkAdapterLayer.tierSecret" -}}
{{- $name := (index .ctx.Values.networkAdapterLayer.keys .tier).existingSecret | default "" -}}
{{- if not $name -}}
{{- fail (printf "%s: networkAdapterLayer.keys.%s.existingSecret is required -- the %s tier signs as its own identity, from its own Secret (the six keys under keys.existingSecret)." .ctx.Chart.Name .tier .tier) -}}
{{- end -}}
{{- $name -}}
{{- end }}
