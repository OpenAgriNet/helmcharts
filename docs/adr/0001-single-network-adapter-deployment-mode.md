# ADR-0001 — One Beckn OAN network adapter process for all three tiers, by default

**Status:** Proposed
**Date:** 2026-10-15
**Issues:** OpenAgriNet/helmcharts#55, OpenAgriNet/helmcharts#66, OpenAgriNet/network-adapter#55 (parent OpenAgriNet/engineering-tracker#171)

## Context

The Beckn OAN network adapter runs in three tiers: consumer (`bap`, 9202), network (`bpp`, 9201) and provider (`bpp`, 9200). Each was its own container in `quick-start/` and its own Helm release in `charts/adapter-service`, installed once per role. The release name is the Service name the other tiers address.

So every request crossed two containers or pods, with a network hop and a DNS lookup:
- consumer to network for `discover`;
- consumer to provider for `select`;
- provider to network for `publish`.

Every environment also ran three of every object: Deployment, Service, ConfigMap, and the PDB or HPA where enabled.

OAN is single-sided today: our app calls out, and our own discovery and upstream APIs answer. The separate scaling and ownership the split allows is not needed.

Constraints:
- **The registry cannot update a row.** The identities and participant ids already written must keep working.
- **The published Beckn OAN network adapter image must work unchanged.**

## Decision

Two deployment modes from the same chart, compose file, keys and registry rows. **`single` is the default; `multi` (the three adapters, unchanged) is optional.**

In `single`, one Beckn OAN network adapter process listens on **9201** (the multi network tier's port) and mounts four modules. Each module is its tier's module from the tier configs, with the same steps and its own identity and Secret:

| Module | Path | Steps | Routes to |
|---|---|---|---|
| consumer | `/` (app calls `/<action>`) | validateSchema, addRoute, sign | `localhost:9201/_internal/network` (discover), `localhost:9201/_internal/provider` (select, init, confirm, status) |
| network | `/_internal/network/` | validateSign, addRoute | discovery |
| provider | `/_internal/provider/` | validateSign, validateSchema, capability steps | answers itself |
| provider-publish | `/publish` | validateSchema, addRoute, sign | `localhost:9201/_internal/network/publish` |

A change complies with this decision if:
- **Paths:** the paths callers see stay `/discover`, `/publish`, `/<action>` and `/health`, the same as multi. Tier-to-tier paths stay under `/_internal/`, called only by the process itself.
- **Routing:** each routing module keeps its own routing file (`routing-network-adapter-consumer.yaml`, `routing-network-adapter-provider.yaml`; network reuses `routing-network.yaml`). A module's routing file is its list of allowed actions: `addRoute` runs before `sign`, so an action with no rule errors instead of being forwarded.
- **Helm:** no new role. Role `network` with `networkAdapterDeploymentMode: single` (the default) is the single adapter; with `multi` it is the network tier. `provider` and `consumer` ignore the value. Both modes release `network-adapter` into namespace `network-adapter`. Routing is generated at render time from the existing `config/routing-*.yaml`, with hosts rewritten to `localhost`. The render fails on:
  - a literal private key;
  - `http.port` ≠ `service.targetPort`;
  - an unset tier Secret name;
  - an Ingress path other than single-segment `/<action>`.
- **Secrets:** `openbao-secrets` reflects the consumer and provider key Secrets and `provider-upstream` into `network-adapter` for single mode. A multi environment removes those targets and deletes the copies, so the network tier holds only its own key.
- **Choosing the mode:** the chart value `networkAdapterDeploymentMode: single | multi` on the `network-adapter` release; multi also installs `consumer-adapter` and `provider-adapter`. In quick-start, `NETWORK_ADAPTER_DEPLOYMENT_MODE` picks `network-adapter`'s config, and profile `multi` adds the other two adapters, via `make up` and `make up-multi`.

## Alternatives considered

- **Keep three adapters only.** It keeps three of every object and a cross-pod hop on every request, for a separation a single-sided deployment does not use.
- **In-process transport (`inproc://`) between modules.** It needs an adapter image that includes the transport, so it fails the "image unchanged" constraint. The gain does not pay for that dependency: the `localhost` hop was already sub-millisecond on Docker, the prototype saved about 0.1–0.3 ms per hop, and an end-to-end benchmark showed no measurable gain.
- **Tier prefixes in the public paths** (`/network/`, `/provider/`, `/provider/publish`). This was the first version. It put the tiers back into the URLs callers use, gave single and multi different URLs, and needed a per-path allow-list at the edge instead of one `/_internal` deny. Replaced after review.
- **One Helm role `network` with a mode switch.** With `single` as the default, every existing `role: network` release would become a three-identity adapter on upgrade and fail without the consumer and provider Secrets. It also makes every existing network-tier code path depend on a new setting.
- **One shared routing file for the consumer and publish modules.** The publish module's rule is for the empty action `""`. Shared, it would also apply to the consumer, so `POST /` on the public consumer would publish into the catalogue.
- **Port 9200.** That is the multi provider's port. 9201 is the multi network tier's port and already the chart's default `service.port`.

## Consequences

**Gains:**
- One Deployment, Service, ConfigMap and PDB per environment.
- No hop between pods.
- No registry, identity or image change.
- Reversible: install the multi releases next to the single one, repoint the app, remove the single release.

**Costs:**
- **Scaling is per adapter, not per tier.** An environment that needs a tier scaled on its own uses `multi`.
- **One crash takes all three tiers down.** Replicas (3 in the example), the PDB and the spread constraint limit the blast radius.
- **Tier configs exist twice.** Each module in `network-adapter-config.yaml` mirrors its tier file, so a change meant for both modes is made in both.
- **The catalogue publisher's port differs by mode:** `:9201/publish` in single, `:9200/publish` in multi.

**Security:**
- `/publish` has no `validateSign`, and `/_internal/` is internal. Their protection is path-level:
  - **Helm:** the Ingress guard;
  - **quick-start:** the `server_proxy.conf` denies.
- Inside the cluster, anything that reaches port 9201 can call `/publish`. A NetworkPolicy cannot isolate it, because it has no notion of paths. Port-level isolation would need a second listener, which is not built.

**Outside this repository:** `OpenAgriNet/network-adapter`'s own `install/` and `config/` still describe the per-role layout. Retiring them is the remaining part of network-adapter#55.

**Verification (2026-10-22, flat paths, port 9201):** quick-start single (11/11 assertions) and multi (10/10), and k3d single (12/12), all passing: publish, discover and select 200, tier hops on `localhost:9201/_internal/<tier>`, and on k3d an unsigned call to `/_internal/network/discover` refused with 401.
