# ADR-0004 — The Descriptor's `schemaPacks` points to one schema-pack file

**Status:** Proposed
**Date:** 2026-10-28
**Issues:** OpenAgriNet/network-adapter#74, OpenAgriNet/helmcharts#82

## Context

The architecture doc puts `schemaPacks` in the Descriptor as a list of pack
URLs. The team agreed instead to publish the list as its own file, so the
list can change without the Descriptor growing with it.

## Decision

1. The list is published at `/.well-known/schema-packs.json` as
   `{"schemaPacks": [ ... ]}`.
2. The Descriptor's `schemaPacks` is that file's URL, on the network's own
   domain. A fetcher reads the file from the network's own host, over HTTPS.
3. The pack URLs inside the list can be on any https host.

## Alternatives considered

- **The list inside the Descriptor.** Matches the architecture doc and puts
  the list under the signature, but every pack change becomes a new signed
  Descriptor, and it reverses the shape the team agreed.
- **A digest of the file inside the signed Descriptor.** Would cover the list
  with the signature, but adds a field every implementation must produce and
  check. Not taken, to keep the contract simple.

## Consequences

- The list is protected by HTTPS and by being served from the network's own
  domain, not by the Descriptor's signature. Whoever can change files on that
  host (the reverse proxy, a CDN in front of it) can change the list without
  breaking the signature.
- An admission only ever covers packs the operator names, so a changed list
  cannot widen an existing agreement; it can only change what is on offer at
  the next `admit`.
