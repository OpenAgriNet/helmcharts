# Postman collection

`OAN-dev-flow.postman_collection.json` — publish, discover and select, for
each of the two capabilities, against a stack brought up from the compose file
beside it. Six requests, 32 assertions.

Import it and run it. **There is nothing to fill in.** Every variable is
prefilled with what this deployment actually uses, including the two provider
ids, so the binding keys in the payloads already match the adapter that will
serve them.

## There are no registry requests here

Deliberately. The registry has no route through the gateway and publishes on
loopback only, so a Postman request could not create or read a row from
outside the VM. `bin/setup.py` seeds all of it — five participants and both
capability bindings — which is what makes this collection short.

So the prerequisite is the stack being up the normal way:

    make up

## Run them in order the first time

Requests 1 and 2 publish the catalogues that 3 and 4 search for. After that
any request works on its own. Re-running is safe: publish is idempotent from
the caller's point of view, and nothing here writes to the registry.

## What the assertions actually check

Enough that a green run means the stack is healthy, not just answering:

- publish comes back `ACCEPTED`
- discover returns at least one catalogue, and for mandi that the specific
  catalogue just published is the one found
- a discovered catalogue advertises `OnDemand` and carries no prices — the
  pack forbids them in that mode
- select answers with one resource per forecast day, and one per price record
- the mandi answer is in `Direct` mode with every field the pack requires of
  it, its prices are numbers rather than the strings the upstream sends, and
  `arrivalDate` is ISO rather than the `dd-MM-yyyy` that arrived
- a record with no minimum or maximum omits those fields instead of sending
  nulls — the last mock record is built that way on purpose
- status codes come from the spec's `DRAFT|ACTIVE|CLOSED` enum
- every resource carries a `quantity`
- the weather answer names no party in either direction, and its offer
  references only resources actually returned

## Requests 5 and 6 are the interesting pair

They hit **the same endpoint on the same adapter**, and different domain
packages answer them. Each provider step builds a binding key from the payload
it is given — provider id plus capability `@type` — serves it if the key is
its own, and passes it through untouched if not. Nothing routes by URL, by
path or by domain, which is what lets one adapter host both capabilities and
what makes adding a third a config change.

## Tunnelling

The ports are on the VM's loopback. From a workstation:

    ssh -L 9202:127.0.0.1:9202 -L 9200:127.0.0.1:9200 -N you@the-vm

Those two are all the collection needs: 9202 is the experience adapter, 9200
the provider adapter, which is where a publish enters.
