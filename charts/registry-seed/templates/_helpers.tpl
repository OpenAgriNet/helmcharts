{{/*
# ============================================================================
# OAN REGISTRY-SEED CHART HELPERS
# Owner: OpenAgriNet Engineering Team
# Purpose: chart-local helpers that delegate to common.
# ============================================================================
*/}}

{{- define "registry-seed.name" -}}
{{- include "common.name" . -}}
{{- end }}

{{- define "registry-seed.fullname" -}}
{{- include "common.fullname" . -}}
{{- end }}

{{- define "registry-seed.labels" -}}
{{- include "common.labels" . -}}
{{- end }}

{{- define "registry-seed.selectorLabels" -}}
{{- include "common.selectorLabels" . -}}
{{- end }}

{{/*
The seed document the Job reads.

Built here rather than in the ConfigMap template so the validation below runs
once and reports every problem before anything is written. The registry is
append-only with a soft delete that keeps the unique index, so a row seeded
from a wrong value cannot be corrected or replaced -- only abandoned under a
new id. That makes a render-time refusal much cheaper than a successful write.
*/}}
{{- define "registry-seed.document" -}}
{{- $root := . -}}
{{- if not .Values.registry.url }}
{{- fail (printf "%s: registry.url is required" .Chart.Name) }}
{{- end }}
{{- if not .Values.keycloak.url }}
{{- fail (printf "%s: keycloak.url is required" .Chart.Name) }}
{{- end }}
{{- if not .Values.seedUser.passwordSecret.name }}
{{- fail (printf "%s: seedUser.passwordSecret.name is required - this chart renders no passwords" .Chart.Name) }}
{{- end }}
{{- range .Values.adapters }}
{{- if or (not .participantId) (not .baseUrl) (not .signingPublicKey) }}
{{- fail (printf "%s: adapter %q needs participantId, baseUrl and signingPublicKey. The public key is published to peers and is what every signature this participant makes is verified against, so it cannot be defaulted." $root.Chart.Name (.role | default "?")) }}
{{- end }}
{{- if hasPrefix "base64:" .signingPublicKey }}
{{- fail (printf "%s: adapter %q signingPublicKey carries a `base64:` label. It is published verbatim and a verifier hands it straight to a decoder, so the label fails every verification with an error pointing nowhere near the registry." $root.Chart.Name .role) }}
{{- end }}
{{- end }}
{{- range .Values.upstreams }}
{{- if or (not .participantId) (not .baseUrl) }}
{{- fail (printf "%s: upstream %q needs participantId and baseUrl" $root.Chart.Name (.participantId | default "?")) }}
{{- end }}
{{- end }}
{{- range .Values.bindings }}
{{- if or (not .participantId) (not .capability) (not .path) (not .mappingUrl) }}
{{- fail (printf "%s: binding %q needs participantId, capability, path and mappingUrl" $root.Chart.Name (.capability | default "?")) }}
{{- end }}
{{- end }}
{{/*
Refuse a value still in its <describe-it-here> form.

Ported from setup.py's is_placeholder(). Every placeholder in an example is
angle-bracketed and no real id, URL or path can be, so this is an exact test
rather than a guess -- and it is worth having because seeding a row from a
placeholder burns that id permanently: the registry cannot update a record and
its delete is soft, so the capability could only be recovered under a NEW id.

Matched ANYWHERE in the value, not just as a whole one. A host assembled from
a template -- `https://consumer.<IP>.sslip.io` -- neither starts with `<` nor
ends with `>`, so a prefix/suffix test waves it through and the unfilled
address is seeded for good. That is the exact mistake this guard exists to
stop, and it is the likely shape of one now that these values are composed
rather than written out whole.
*/}}
{{- $placeholders := list -}}
{{- range .Values.adapters }}
{{- range $k, $v := (dict "participantId" .participantId "baseUrl" .baseUrl "signingPublicKey" .signingPublicKey) }}
{{- if regexMatch "[<>]" $v }}
{{- $placeholders = append $placeholders (printf "adapters[].%s = %s" $k $v) }}
{{- end }}
{{- end }}
{{- end }}
{{- range .Values.upstreams }}
{{- range $k, $v := (dict "participantId" .participantId "baseUrl" .baseUrl) }}
{{- if regexMatch "[<>]" $v }}
{{- $placeholders = append $placeholders (printf "upstreams[].%s = %s" $k $v) }}
{{- end }}
{{- end }}
{{- end }}
{{- range .Values.bindings }}
{{- range $k, $v := (dict "path" .path "mappingUrl" .mappingUrl "capability" .capability) }}
{{- if regexMatch "[<>]" $v }}
{{- $placeholders = append $placeholders (printf "bindings[].%s = %s" $k $v) }}
{{- end }}
{{- end }}
{{- end }}
{{- if $placeholders }}
{{- fail (printf "%s: these are still example placeholders:\n    %s\n  Fill them in and re-render. Nothing has been written yet, which is the point: this registry is APPEND-ONLY - it cannot update a record, and its delete is soft and keeps the unique index - so seeding a row from a placeholder burns that id permanently. The capability could then only be recovered under a new id." .Chart.Name (join "\n    " $placeholders)) }}
{{- end }}
{{- $doc := dict
      "registryUrl" .Values.registry.url
      "keycloak" (dict "url" .Values.keycloak.url "realm" .Values.keycloak.realm "clientId" .Values.keycloak.clientId "username" .Values.seedUser.username)
      "adapters" .Values.adapters
      "upstreams" .Values.upstreams
      "bindings" .Values.bindings
      "schemas" .Values.schemas
      "schemaPackRef" .Values.schemaPackRef
      "validity" .Values.keyValidity -}}
{{- toPrettyJson $doc -}}
{{- end }}
