# ADR-0002 — Tier mounts carry the tier name; the edge rewrites the public path

**Status:** Proposed
**Date:** 2026-10-27
**Issues:** OpenAgriNet/helmcharts#72 (supersedes the path rules in ADR-0001)

## Context

ADR-0001 put the consumer tier on `/` and the other two under `/_internal/`,
and rejected tier prefixes in the public paths. Under its own premise that was
right:

> OAN is single-sided today: our app calls out, and our own discovery and
> upstream APIs answer.

Federation ends that premise. Another network now calls **in**, and it calls
the address our Network Descriptor advertises: `services.discovery`, which is
`baseUrl` + `/discover`.

With the consumer tier on `/` as the catch-all, that inbound `/discover`
matched the consumer mount. Its steps are `validateSchema, addRoute, sign` --
no `validateSign`, no `checkCrossNetworkRequester`. The tier that checks both
sat at `/_internal/network/`, which the edge denied outright.

Measured on two local networks that had admitted each other:

    unsigned POST /discover                    -> 500, no authentication ran
    unsigned POST /_internal/network/discover  -> 401 AUT_SIGNATURE_MISSING

So the door another network was told to use checked nothing, and the door that
checked was sealed. Not a code defect -- `checkCrossNetworkRequester` works --
but a mount layout that routed inbound traffic past it.

The `/_internal` name also made this hard to see. It reads as "nobody calls
this", when in fact it is the only thing another network should call.

## Decision

**The tiers mount on `/consumer/`, `/network/` and `/provider/`, in both
deployment modes. The edge rewrites the public `/<action>` onto the mount that
serves it.**

    public                      internal
    /discover            ->     /network/discover
    /select, /init, ...  ->     /provider/<action>

ADR-0001's promise is kept: **the paths callers see do not change.** `/discover`
and `/select` are still what another network calls, and single and multi still
present the same URLs. Only the names inside the process change, and the edge
learns a mapping it did not need when one mount answered everything.

`NETWORK_BASE_URL` therefore stays bare -- no `/network` suffix -- because the
descriptor advertises the public path, not the mount.

A change complies with this decision if:

- **Mounts:** `/consumer/`, `/network/`, `/provider/`, and `/publish` for the
  catalogue system. No catch-all `/` mount: an action with no mount is a 404,
  not whatever the first prefix match happens to be.
- **Checks:** every mount another network can reach lists `validateSign` and
  `checkCrossNetworkRequester` before anything else, and `sign` last.
  `/consumer/` and `/publish` list neither, by design -- their caller is this
  deployment's own software, inside the trust boundary.
- **The edge:** `/consumer/` and `/publish` are never reachable from outside.
  Any other mount may be, and a rewrite onto it is allowed. The chart validates
  the rewrite **target**, refusing only those two; it does not enumerate what
  is permitted, so a mount added later needs no chart change.
- **No controller is named.** The chart emits a plain Ingress and takes the
  rewrite annotations from values. Kong, ingress-nginx and ALB spell the
  rewrite differently, and a chart that only works behind one of them is not
  portable. quick-start does the same job in its own nginx config.

## Alternatives considered

- **Keep `/` and deny `/_internal`** (ADR-0001 as written). Fails the moment
  another network calls in: the catch-all answers them, unchecked. Only
  defensible while nothing external can call the adapter.
- **Put `/network/` in the public URL** (`NETWORK_BASE_URL` ending `/network`).
  This is what quick-start shipped first, and it works -- no rewrite needed
  anywhere. Rejected because it breaks ADR-0001's path promise, leaks an
  internal name into every other network's configuration, and makes the
  published address depend on our deployment mode.
- **Keep the blanket ban on rewrite annotations.** The ban existed because a
  loose rewrite could reach `/_internal` or `/publish`. Refusing the two unsafe
  targets achieves that without also forbidding the mapping this decision
  needs, and without the chart having to know every mount that will ever exist.
- **An allow-list of `/network/` and `/provider/`.** Safe, but it makes the
  chart the authority on a deployment's topology and needs a chart release to
  add a mount. A deny-list of the two unsafe targets is the smaller claim.

## Consequences

**Gains:**
- Another network's call reaches a tier that verifies the signature and the
  admission. That was the defect this decision exists to fix.
- Mount names say who calls them. `/_internal/network/` said the opposite of
  the truth.
- One mount layout across quick-start, the charts and `networkofnetwork`.

**Costs:**
- The edge now carries a mapping. An environment that forgets it serves 404 on
  `/discover` -- loud, but a new way to be wrong.
- The rewrite is spelled differently per ingress controller, so the values
  differ by environment even though the chart does not.
- `NETWORK_BASE_URL` values already published cannot be corrected in place for
  anyone who has admitted us: they hold our descriptor at a revision. Raising
  the revision and re-publishing is the path, and admitted networks pick it up
  on refresh.
