# ADR-0002 — Federation admission is a per-direction record on the peer's Participant entry

**Status:** Proposed
**Date:** 2026-10-28
**Issues:** OpenAgriNet/helmcharts#71, OpenAgriNet/helmcharts#72, OpenAgriNet/network-adapter#75

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

Sunbird RC validates a PUT against the request body, not against the record
the merge will produce, and then merges the body into what it stores. It
stamps `osid` and `os*` fields into each nested object.

## Decision

1. Admission lives on the peer's `Participant` entry as `federationRole`,
   keyed by direction: `source` (we consume from the peer) and `requester`
   (the peer consumes from us). Either, both, or neither may be present.
   Each direction has its own definition, `FederationSource` and
   `FederationRequester`.
2. A direction holds `schemaPacks` (at least one), `descriptorRevision`,
   `admittedAt` and `updatedAt`. Nothing else: both definitions are
   `additionalProperties: false`.
3. Only our operator decides admission. Nothing read from a peer — its
   Descriptor or its calls — creates a direction or adds packs. Admission is
   between two networks and never passes along a chain.
4. **A direction is withdrawn by removing it**, not by marking it. There is no
   per-direction lifecycle, so a suspended direction and one never admitted
   are the same record.
5. The peer's other facts stay at the entry's top level: `revision`, `baseUrl`,
   `projectionTtl` and `keys`. `baseUrl` carries the Descriptor's
   `services.discovery` and is required on a node. `projectionTtl` is the
   seconds the peer lets us keep what we crawled, and belongs to the
   participant rather than to a direction.
6. A peer's key is named by the `osid` of the key itself, not of the entry.
   A verifier filters by `participantId` and then matches each key's `osid`
   against the id the signature names.
7. `role` accepts a single string or an array, so a row written before the
   list form still reads.

## Alternatives considered

- **A status on each direction.** Keeps the record of what was admitted and
  withdraws it in one write. Not implemented here; see Consequences.
- **The entry's osid as the key id.** One id for the record and the key,
  which reads simply until a peer rotates a key and the two cannot both move.

## Consequences

- Withdrawing a direction loses what was agreed: the packs, the revision and
  the date go with it. Re-admitting is the only way back, and it records a
  new `admittedAt` rather than restoring the old one.
- **`federationRole` sets `minProperties: 1`, and `suspend` clears the object
  to `{}` before writing back the directions it keeps.** That write is refused
  by this schema. Suspending the last direction therefore cannot succeed
  against a registry that validates, and the script's tests do not catch it
  because the double does not validate. This is a live gap, recorded here
  rather than discovered again.
- A peer must publish a key id our registry can store as an `osid`.
