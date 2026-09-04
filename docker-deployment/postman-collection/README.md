# Postman collection

Two files. Import both.

    OAN-dev-flow.postman_collection.json    the requests
    OAN-dev.postman_environment.json        where your deployment's URLs go

**The collection alone works against a tunnel.** Every URL variable defaults to
loopback, because the stack publishes its ports on the VM's loopback only:

    ssh -L 9202:127.0.0.1:9202 -L 9200:127.0.0.1:9200 \
        -L 8081:127.0.0.1:8081 -L 8080:127.0.0.1:8080 -N you@the-vm

**The environment is how you point it somewhere else.** Import it, select it in
the environment dropdown, and edit the six URLs at the top — the experience,
network and provider adapters, the registry, Keycloak and discovery. Postman
resolves an environment variable ahead of a collection variable of the same
name, so nothing in the collection needs touching and the loopback defaults
stay intact for the next person.

No VM hostname or address is committed in either file. Deployment addresses are
shared separately, and the environment file is the place to put them.

## The 19 requests

    1      get a write token                          saves {{token}}
    2-4    create the exp / network / provider nodes
    5-8    create both upstreams and both bindings
    9-10   update an upstream's URL              (PUT)
    11     update a binding's call plan          (PUT)
    12-13  search participants / provider bindings
    14-19  publish, discover, select -- both capabilities

Run in order the first time: request 1 issues the token that 2-11 need, and the
publish requests seed the catalogues discover looks for.

## A default run changes nothing

That is deliberate, so the collection is safe to re-run against a live stack.

- **The creates** report "already present". The registry is append-only -- no
  update on create, and a soft delete keeps the unique index -- so a second run
  cannot succeed. The test accepts a duplicate and fails anything else, so a
  validation error or a bad token is still caught.
- **The updates** write back the same value the variable already holds. Change
  a variable to actually change a row.

## What the updates need

`PUT /api/v1/{Entity}/{osid}` works, and a **partial body merges** -- send only
`baseUrl` and the name, type and status keep their stored values.

Two things follow from how the registry implements it:

- **The URL takes an osid, not a participantId.** The registry addresses a
  record by the id it assigned on write. Each PUT resolves that itself in a
  pre-request script, so the request still runs on its own.
- **The entity schema has to permit additional properties.** The registry
  re-validates the *merged* document, and that document carries the `osid`,
  `osUpdatedAt` and `osOwner` it injected itself -- which `additionalProperties:
  false` rejects as extraneous, naming its own fields. `Participant`,
  `ProviderSchema` and `ActionBinding` in `config/registry/schemas/` allow them
  for this reason. `PublicKey` deliberately does not: nothing here updates key
  material, and a partial PUT that omits `keys` never re-validates it.

Schemas are read at startup, so a change there needs the registry restarted.

## Filling in the node keys

`expNodeKey`, `networkNodeKey` and `providerNodeKey` ship blank, because the
keypairs are generated per deployment into `keys/keys.json` on the host.
`bin/setup.py` already created those three rows, so requests 2-4 are normally
not needed at all -- they are here to show what a node record looks like. With
the keys blank they report "awaiting a key" rather than failing the run.

If you do fill them in, use the public half exactly as `keys/keys.json` holds
it: bare base64, no encoding label. A node created with a key the adapter does
not hold produces signatures nobody can verify, and the id cannot be reclaimed.

## networkAdapterUrl

Present as a variable, used by no request. Discover reaches the network adapter
through the experience adapter and publish through the provider adapter, so
nothing here calls it directly. It is there because it is the other adapter a
deployment exposes publicly: its `/publish` and `/discover` both verify
signatures, so a network peer calls it directly. Signing is not something
Postman does, so those calls are not scripted.
