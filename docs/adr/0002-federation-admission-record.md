# ADR-0002 — Federation admission is a per-direction record on the peer's Participant entry

**Status:** Proposed
**Date:** 2026-10-28
**Issues:** OpenAgriNet/helmcharts#71, OpenAgriNet/network-adapter#75

## Context

A network admits a peer network before anything is shared with it. The
admission has to say which way sharing goes (we read from them, they read
from us) and which schema packs each way covers, and the adapter reads it on
every inbound call and every crawl. The architecture doc also asks that a
disputed interaction can be reconstructed later, including the admission that
applied at the time.

The peer publishes its own facts in a signed Descriptor: base URL, keys,
revision, how long its data may be kept. Those change on the peer's schedule.
Admission changes only when our operator decides.

Sunbird RC re-validates the whole merged document on a PUT, nested objects
included, and stamps `osid` and `os*` fields into each nested object it
stores.

## Decision

1. Admission lives on the peer's `Participant` entry as `federationRole`,
   keyed by direction: `source` (we consume from the peer) and `requester`
   (the peer consumes from us). Either, both, or neither may be present.
   Both directions use one definition, `FederationAdmission`.
2. Each direction holds `schemaPacks` (at least one), `descriptorRevision`,
   `admittedAt`, `updatedAt`, `status` and `suspendedAt`. `source` also
   holds `projectionTtl`, the seconds the peer lets us keep its data;
   `requester` may not carry it.
3. Only our operator decides admission. Nothing read from a peer, its
   Descriptor or its calls, creates a direction, adds packs or changes
   `status`. A refresh from the Descriptor updates the peer's facts and,
   inside `source`, only `projectionTtl`, which limits what we may keep and
   cannot widen anything. Admission is between two networks and never passes
   along a chain.
4. A direction is withdrawn by setting `status` to `suspended` with
   `suspendedAt`. The direction stays in the record. A missing `status` means
   `active`.
5. Other facts the peer declares stay at the entry's top level: `revision`,
   `baseUrl`, `discoveryUrl` and `keys`. `discoveryUrl` is the Descriptor's
   `services.discovery`, called as is; `baseUrl` keeps meaning the base an
   action is appended to. A top-level `revision` above a
   direction's `descriptorRevision` means the peer changed since that
   direction was admitted and is due for review.
6. Closed nested objects (`federationRole`, `FederationAdmission`,
   `PublicKey`) keep `additionalProperties: false` and declare the Sunbird
   system fields, so a PUT passes and a misspelt field is still refused.
7. `role` is an array of `Role`.
8. A peer's key carries the peer's own `kid` as `keyId`, any non-empty
   string. Our registry's `osid` stays the local record id. A verifier
   matches `keyId` first and falls back to `osid`, which is what our own
   keys are named by. A peer does not have to run Sunbird RC.

## Alternatives considered

- **`federationRole` as a single string** (`source`, `requester`, `both`).
  Cannot carry per-direction packs, revision or time, and cannot express
  "admitted one way, suspended the other".
- **A separate `Admission` entity.** Puts a second lookup on every inbound
  call, and the two records can disagree about whether a peer exists.
- **Suspend by deleting the direction.** Loses `admittedAt` and
  `descriptorRevision`, which the attribution requirement needs. It also
  needs `federationRole: {}` as an intermediate write when Sunbird merges
  objects, which `minProperties: 1` rejects.
- **`projectionTtl` at the entry's top level.** Exists on upstreams and
  requester-only peers, where it means nothing, and sits away from the
  agreement it limits.
- **A peer's `kid` as our `osid`.** Rejects any peer whose `kid` is not a
  Sunbird record id, which the architecture doc does not require; its own
  example is `governance-1`. Sunbird also keeps a supplied nested `osid` only
  on create, so a rotated key could not be filed under its new `kid`.
- **The discovery URL in `baseUrl`.** `baseUrl` already means the base an
  action is appended to, so a caller appending `/discover` gets
  `/discover/discover`.
- **`additionalProperties: true` on nested objects.** Lets the system fields
  through but also any typo, which then fails silently at read time.
- **`role` as string or array.** Every reader handles two shapes forever.

## Consequences

- The adapter must treat a `suspended` direction as not admitted, for both
  the inbound requester check and the crawler. Until it does, setting
  `suspended` does not stop sharing.
- Sunbird merges nested objects on update: a field the update leaves out
  keeps its stored value, and an empty object clears everything in it. So
  admitting a suspended direction again must write `status: active`
  explicitly, `suspendedAt` stays as the last suspension, and an empty
  `federationRole` must never be sent.
- On update, Sunbird enforces types, enums, `additionalProperties`,
  `minItems` and `not`, but not `required`, `minProperties` or `if`/`then`
  (checked against a live registry). Writers must keep those rules
  themselves, such as sending `suspendedAt` with `suspended`, and their tests
  should validate every payload against this schema.
- A direction stored before `status` existed has none and reads as
  `active`, so no migration is needed. Sunbird does not apply the schema
  `default`; readers must.
- The adapter must not apply this check to participants outside federation.
  Our own consumers and providers carry no `federationRole` and must still
  be served.
- A row stored with `role` as a string keeps working until its next update,
  which then fails until it is rewritten as a list.
- The quick-start schema and the registry chart's copy must stay identical.
- If Sunbird adds a system field, every closed nested object needs it
  declared, or updates to that entry start failing.
- A top-level `projectionTtl` written before this change is still accepted
  (the entry's top level allows extra fields) but is not read. Readers take
  it from `source`.
- Removing a value the peer stops declaring (`projectionTtl`,
  `discoveryUrl`) is not possible through an update; the last value stays.
- `keyId` is not checked for uniqueness across keys by the schema. The
  admission tool must refuse a peer whose JWKS repeats a `kid`.
- The adapter's key lookup and the crawler change with this record: match
  `keyId` then `osid`, read `projectionTtl` from `source`, and call
  `discoveryUrl` (falling back to `baseUrl` + `/discover`).
