# ADR-0002 — Filling the registry: create-only rows, and what that costs

**Status:** Accepted
**Date:** 2026-10-28
**Issues:** OpenAgriNet/helmcharts#72

## Context

A network's registry holds what the adapters read at runtime: who this network
is, which upstreams it calls, and what each capability means. `quick-start`
fills it with `bin/setup.py`, one command per kind of row.

The registry is Sunbird RC, and it does not overwrite a row that exists. A
wrong value is corrected by a PUT against the stored record, not by running the
command again. Delete is soft and keeps the unique index, so an id that was
used is used forever.

## Decision

1. **Every command creates only.** A row that exists is left alone and reported
   as already present. This is why registering runs one provider at a time: a
   wrong value in `.env` costs that provider, not the network.
2. **`baseUrl` on our own node is the address we publish**, taken from `.env`
   rather than built from the id. Nothing inside the stack resolves it —
   routing between adapters uses compose service names — so a wrong value costs
   nothing locally and everything on a shared network, where it is the address
   another network posts to.
3. **`actions` is a list, not a map.** The registry treats every nested object
   as an entity and gives it an osid, which a map cannot carry. It also lets one
   action be retired without touching the others.
4. **`mappings` is one reference covering both directions**, because the
   response mapping reads what the request mapping resolved.
5. **`method` is per provider**, read from the row. It is the provider's
   contract rather than a network convention: some answer a GET whose query the
   mapping builds, others take a POST with a JSON body.
6. **`schemaUrl` is derived from the capability code**, which already names the
   pack directory, so the two cannot drift. The host is
   `openagrinet.github.io`, which is what catalogs carry in their `@context`; a
   URL derived from `raw.githubusercontent.com` matches nothing at crawl time.

## Alternatives considered

- **Make the commands idempotent by updating.** The registry's own model is
  create-once with corrections by PUT, and a command that silently rewrote a
  row would hide the one mistake worth seeing: an id already in use.
- **Build `baseUrl` from the participant id.** Convenient locally and wrong on
  a shared network, where the published address is not always the id's host.

## Consequences

- A wrong `schemaUrl` is permanent for that capability code. `SchemaRegistry`
  sets `additionalProperties: false`, so an update is refused over the system
  fields the registry merges in, and delete keeps the unique index.
- The registry never fetches `schemaUrl`; it only pattern-checks the shape. A
  row pointing at a ref that serves nothing stores happily and fails later, at
  whoever resolves it.
- Re-running a command after fixing `.env` does not fix the row that was
  already written.
