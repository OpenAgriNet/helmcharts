# ADR-0003 — A network has one identity, shared by all three adapter tiers

**Status:** Proposed
**Date:** 2026-10-28
**Issues:** OpenAgriNet/network-adapter#74, OpenAgriNet/helmcharts#82

## Context

Quick-start seeded three registry entries and three keypairs, one per adapter
tier (consumer, network, provider), each with its own participant id.

Federation names a network by one id: the Descriptor's `networkId`, the
domain its `/.well-known/` documents are served from. A peer verifies a
signed call by looking up the sender's id and key in its own registry, and
admits a network, not a tier. With three identities, a peer would need to
know which of our three ids and keys belong to the network it admitted, and
our JWKS would publish keys for ids the Descriptor never names.

## Decision

1. A network has one registry entry, with `participantId` = `BECKN_NETWORK_ID`
   (its domain) and `role` = `["consumer", "provider", "network"]`.
2. It has one keypair, in `keys/keys.json`. Every adapter tier signs with it
   and names it by the key's `osid`, in single-adapter and multi-adapter mode
   alike.
3. The Descriptor, the JWKS and every signed call carry that one id and key.

## Alternatives considered

- **Keep three identities and publish all three keys.** The JWKS would hold
  keys for ids the Descriptor does not name, and a peer could not tell which
  id an admission covers. Admitting a network would mean admitting three
  participants.
- **Identity for the network tier only.** The consumer and provider tiers
  still sign calls a peer receives, so they would need keys a peer cannot
  find through the Descriptor.

## Consequences

- A stack set up before this has a `keys/keys.json` holding three identities.
  `setup.py` refuses it with a message naming `make destroy`; there is no
  in-place migration.
- The three old participant ids stay in such a registry, which cannot truly
  delete a row. They are harmless but never used.
- A tier is no longer identifiable from the signature alone. Anything that
  needs the tier reads it from the request, not the sender.
- Key rotation now happens once for the whole network.
