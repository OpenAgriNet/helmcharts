{{/*
# ============================================================================
# role: network-layer -- the unified single adapter.
#
# The consumer, network and provider tiers as four modules of ONE adapter
# process. Nothing here is a second copy of a tier: the config and the routing
# are GENERATED at render time from config/<tier>-config.yaml and
# config/routing-<tier>.yaml, the same files the three multi-adapter roles
# mount. A change to a tier file reaches both modes on the next render.
#
# Mirrors quick-start/bin/setup.py _render_unified_network_layer and
# quick-start/config/adapters/unified-network-layer/overrides.yaml.
# ============================================================================
*/}}

{{/*
"true" when this release runs the unified single adapter, else empty.
*/}}
{{- define "adapter-service.isNetworkLayer" -}}
{{- if eq (include "adapter-service.role" .) "network-layer" -}}true{{- end -}}
{{- end }}

{{/*
Which tier-file module becomes which module of the unified single adapter.
`index` is the module's position in that tier file's `modules:` list.
`routing` is the routing file the module's router reads; the provider module
has no router (it answers requests itself). Topology, not environment, so it
is a constant here rather than a value -- the same rule config/routing-*.yaml
follows.
*/}}
{{- define "adapter-service.networkLayer.moduleMap" -}}
- {from: consumer, index: 0, name: consumer, path: /consumer/, routing: routing-network-layer-consumer.yaml}
- {from: network, index: 0, name: network, path: /network/, routing: routing-network.yaml}
- {from: provider, index: 0, name: provider, path: /provider/}
- {from: provider, index: 1, name: provider-publish, path: /provider/publish, routing: routing-network-layer-publish.yaml}
{{- end }}

{{/*
One tier file, parsed, with its __ADAPTER_*__ placeholders renamed to that
tier's (__CONSUMER_*__, __NETWORK_*__, __PROVIDER_*__) so the init container can
fill each module from its own tier's Secret.
Usage: include "adapter-service.networkLayer.tier" (dict "ctx" $ "tier" "provider") | fromYaml
*/}}
{{- define "adapter-service.networkLayer.tier" -}}
{{- $file := printf "config/%s-config.yaml" .tier -}}
{{- $raw := .ctx.Files.Get $file -}}
{{- if not $raw -}}
{{- fail (printf "%s: role network-layer is generated from %s, which is missing from the chart." .ctx.Chart.Name $file) -}}
{{- end -}}
{{- $doc := $raw | replace "__ADAPTER_" (printf "__%s_" (upper .tier)) | fromYaml -}}
{{- if or (not $doc) (hasKey $doc "Error") -}}
{{- fail (printf "%s: %s does not parse as YAML, so the network-layer config cannot be generated from it." .ctx.Chart.Name $file) -}}
{{- end -}}
{{- toYaml $doc -}}
{{- end }}

{{/*
The unified single adapter's config, as YAML.

Process-level settings (log, http, pluginManager, otelsetup) come from the
consumer tier file, as in quick-start; appName, the listener port, the http
timeouts (http.timeout), the log level (logLevel) and telemetry (otel.*) are
set here from values. Each module is the
tier file's module with only its name, path, routingConfig,
extendedSchema_enabled, httpClientConfig and (when registryCache is on) the
cache plugin and registry cacheTTL changed.
*/}}
{{- define "adapter-service.networkLayer.config" -}}
{{- $ctx := . -}}
{{- $nl := .Values.networkLayer -}}
{{- $rc := $nl.registryCache -}}
{{- if and $rc.enabled (not $rc.addr) -}}
{{- fail (printf "%s: networkLayer.registryCache.enabled needs networkLayer.registryCache.addr (host:port of Redis). Without it the registry logs that no cache plugin is configured and caches nothing." $ctx.Chart.Name) -}}
{{- end -}}
{{- $tiers := dict -}}
{{- range $t := list "consumer" "network" "provider" -}}
{{- $_ := set $tiers $t (include "adapter-service.networkLayer.tier" (dict "ctx" $ctx "tier" $t) | fromYaml) -}}
{{- end -}}
{{- $modules := list -}}
{{- range $s := include "adapter-service.networkLayer.moduleMap" . | fromYamlArray -}}
{{- $src := (index $tiers $s.from).modules -}}
{{- if le (len $src) (int $s.index) -}}
{{- fail (printf "%s: network-layer module %s expects config/%s-config.yaml to have a module at index %d; it has %d." $ctx.Chart.Name $s.name $s.from (int $s.index) (len $src)) -}}
{{- end -}}
{{- $m := deepCopy (index $src (int $s.index)) -}}
{{- $_ := set $m "name" $s.name -}}
{{- $_ := set $m "path" $s.path -}}
{{- $p := $m.handler.plugins -}}
{{- if hasKey $p "router" -}}
{{- if not $s.routing -}}
{{- fail (printf "%s: network-layer module %s has a router but the module map names no routing file for it." $ctx.Chart.Name $s.name) -}}
{{- end -}}
{{- $_ := set $p.router.config "routingConfig" (printf "%s/%s" (include "adapter-service.configDir" $ctx) $s.routing) -}}
{{- end -}}
{{- if and (hasKey $p "schemaValidator") $nl.extendedSchemaEnabled -}}
{{- $_ := set $p.schemaValidator.config "extendedSchema_enabled" ($nl.extendedSchemaEnabled | toString) -}}
{{- end -}}
{{- with $nl.httpClient -}}
{{- $_ := set $m.handler "httpClientConfig" (deepCopy .) -}}
{{- end -}}
{{- if $rc.enabled -}}
{{- $_ := set $p "cache" (dict "id" "cache" "config" (dict "addr" $rc.addr "use_tls" ($rc.useTLS | toString))) -}}
{{- if hasKey $p "registry" -}}
{{- $_ := set $p.registry.config "cacheTTL" $rc.ttl -}}
{{- end -}}
{{- end -}}
{{- $modules = append $modules $m -}}
{{- end -}}
{{- $doc := omit (deepCopy $tiers.consumer) "modules" -}}
{{- $_ := set $doc "appName" (include "adapter-service.appName" .) -}}
{{- $_ := set $doc.log "level" .Values.logLevel -}}
{{- $_ := set $doc.http "port" (int .Values.service.targetPort) -}}
{{- with .Values.http.timeout -}}
{{- $_ := set $doc.http "timeout" (dict "read" (int .read) "write" (int .write) "idle" (int .idle)) -}}
{{- end -}}
{{- with (dig "plugins" "otelsetup" "config" dict $doc) -}}
{{- $cfg := . -}}
{{- $on := $ctx.Values.otel.enabled | toString -}}
{{- $_ := set . "serviceName" (include "adapter-service.otelServiceName" $ctx) -}}
{{- $_ := set . "environment" $ctx.Values.otel.environment -}}
{{- $_ := set . "enableMetrics" $on -}}
{{- $_ := set . "enableTracing" $on -}}
{{- $_ := set . "enableLogs" $on -}}
{{- with $ctx.Values.otel.endpoint -}}
{{- $_ := set $cfg "otlpEndpoint" . -}}
{{- end -}}
{{- end -}}
{{- $_ := set $doc "modules" $modules -}}
{{- /* toYaml drops the quotes the tier files put round each placeholder. Put
       them back: the init container substitutes the raw Secret value, and an
       unquoted value can change type (an all-digit id becomes a number). */ -}}
# GENERATED by adapter-service (templates/_network-layer.tpl) from
# config/{consumer,network,provider}-config.yaml. Edit those, not this.
{{ regexReplaceAll "(?m):[ ]+(__[A-Z][A-Z0-9_]*__)$" (toYaml $doc) ": \"${1}\"" -}}
{{- end }}

{{/*
Fails unless every private-key VALUE in a network-layer config is one of the
tier placeholders. Parsed, not grepped: it reads each module's keyManager.
Usage: include "adapter-service.networkLayer.checkKeys" (dict "ctx" $ "cfg" $parsedConfig)
*/}}
{{- define "adapter-service.networkLayer.checkKeys" -}}
{{- range $m := .cfg.modules -}}
{{- $km := dig "handler" "plugins" "keyManager" "config" dict $m -}}
{{- range $field := list "signingPrivateKey" "encrPrivateKey" -}}
{{- $v := index $km $field | default "" | toString -}}
{{- if and $v (not (regexMatch "^__(CONSUMER|NETWORK|PROVIDER)_(SIGNING|ENCR)_PRIVATE__$" $v)) -}}
{{- fail (printf "%s: network-layer module %s sets %s to a value that is not a tier placeholder. A private key written into config reaches git, the ConfigMap and `helm get values`; use __<TIER>_%s__ and the init container fills it from that tier's Secret." $.ctx.Chart.Name $m.name $field (ternary "SIGNING_PRIVATE" "ENCR_PRIVATE" (eq $field "signingPrivateKey"))) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
Routing for the unified single adapter, as YAML: {<file name>: <routing doc>}.

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
{{- define "adapter-service.networkLayer.routing" -}}
{{- $ctx := . -}}
{{- $loopback := printf "http://localhost:%v/${1}${4}" .Values.service.targetPort -}}
{{- /* Whole URL, anchored both ends: only a tier host (optionally with a
       namespace suffix and port) followed by nothing or a path is rewritten. */ -}}
{{- $tierHost := "^https?://(consumer|network|provider)-adapter(\\.[a-z0-9.-]+)?(:[0-9]+)?(/.*)?$" -}}
{{- $out := dict -}}
{{- range $pair := list (list "routing-consumer.yaml" "routing-network-layer-consumer.yaml") (list "routing-network.yaml" "routing-network.yaml") (list "routing-provider.yaml" "routing-network-layer-publish.yaml") -}}
{{- $src := index $pair 0 -}}
{{- $doc := $ctx.Files.Get (printf "config/%s" $src) | fromYaml -}}
{{- if not $doc.routingRules -}}
{{- fail (printf "%s: config/%s parsed to no routingRules; the network-layer routing is generated from it." $ctx.Chart.Name $src) -}}
{{- end -}}
{{- range $i, $rule := $doc.routingRules -}}
{{- if $rule.target -}}
{{- $url := $rule.target.url | default "" -}}
{{- $url = regexReplaceAll $tierHost $url $loopback -}}
{{- if hasSuffix "/" $url -}}
{{- fail (printf "%s: network-layer routing from config/%s routingRules[%d] ends in a slash (%q). The router appends the action, so that produces //discover." $ctx.Chart.Name $src $i $url) -}}
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
Usage: include "adapter-service.networkLayer.tierSecret" (dict "ctx" $ "tier" "network")
*/}}
{{- define "adapter-service.networkLayer.tierSecret" -}}
{{- $name := (index .ctx.Values.networkLayer.keys .tier).existingSecret | default "" -}}
{{- if not $name -}}
{{- fail (printf "%s: networkLayer.keys.%s.existingSecret is required -- the %s tier signs as its own identity, from its own Secret (the six keys under keys.existingSecret)." .ctx.Chart.Name .tier .tier) -}}
{{- end -}}
{{- $name -}}
{{- end }}
