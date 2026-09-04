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

## Four folders

    1. Registry    13 requests -- token, creates, updates, searches
    2. Publish      2 -- one catalogue per capability
    3. Discover     2
    4. Select       2

**Their order matters on a first run.** The token in Registry is what the
writes after it use, and Publish seeds the catalogues Discover searches for. So
run it top to bottom once; after that any folder runs on its own:

    newman run OAN-dev-flow.postman_collection.json --folder "4. Select"

Each folder carries a description explaining what that leg of the flow does and
what its failures mean.

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

## Giving an upstream its own signing key

The two upstream creates carry a `keys` block, blank by default, and drop it
when the variable is empty. Set `weatherProviderKey` or `mandiProviderKey` to a
provider's **public** signing key and the block is sent.

**Why an upstream may have keys.** The `Participant` schema declares `keys` for
every type. Its only conditional is `if type == "node" then require role and
keys`, and it has no `else` -- so that branch adds requirements for a node and
never forbids keys on an upstream. The adapter accepts such a key as a signer
too: the signature lookup filters on `participantId` alone and never compares
`type`, and `isSigning()` treats an absent `use` as signing, which matters
because this schema drops `use` and lets `alg` carry the purpose.

**Why you would want it.** With a key on that row the provider can sign its own
catalogue and `POST /publish` straight at the **network** adapter, which
verifies the signature against the row. The provider adapter drops out of the
publish path -- and with it the unauthenticated `/publish` it otherwise has to
expose, which is the whole reason the gateway carries a deny rule for that path.

Verified end to end: an upstream record created with an `ed25519` key signed a
catalogue and the network adapter answered `catalog/on_publish` `ACCEPTED`,
while a wrong key, a body tampered with after signing, and a missing
`Authorization` header each came back `401`.

The header a provider has to produce:

    Signature keyId="<participantId>|<key osid>|ed25519",
              algorithm="ed25519",created="<unix>",expires="<unix>",
              headers="(created) (expires) digest",signature="<base64>"

signed over exactly this string -- real newlines, and `BLAKE-512` meaning
BLAKE2b-512, not SHA:

    (created): <unix>
    (expires): <unix>
    digest: BLAKE-512=<base64 of blake2b-512 over the raw body>

The `osid` is the one the registry assigns the key on write, so a provider has
to read it back from a `Participant/search` after registering.

**Add the keys when you create the record.** A partial PUT can add a `keys`
array later but cannot remove or replace one -- the registry is append-only. And
the value is bare base64 matching `^[A-Za-z0-9+/]{43}=$`, no encoding label: a
`base64:` prefix left on the front fails verification later with a decode error
that points nowhere near the registry.

## networkAdapterUrl

Present as a variable, used by no request. Discover reaches the network adapter
through the experience adapter and publish through the provider adapter, so
nothing here calls it directly. It is there because it is the other adapter a
deployment exposes publicly: its `/publish` and `/discover` both verify
signatures, so a network peer calls it directly. Signing is not something
Postman does, so those calls are not scripted.
