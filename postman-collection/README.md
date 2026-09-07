# Postman collection

Two files. Import both.

    api-collection.json                 the requests
    local_postman_environment.json      where your deployment's URLs go

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

## Two folders, one per capability

    1. WeatherObservation    1. Publish   2. Discover   3. Select
    2. MandiPrice            1. Publish   2. Discover   3. Select

Six requests, 32 assertions. Run a folder top to bottom the first time --
Publish seeds the catalogue Discover looks for -- and after that any request
works on its own:

    newman run api-collection.json --folder "2. MandiPrice"

The two Select requests are the pair worth comparing. They hit the same
endpoint on the same adapter and different domain packages answer them, because
each provider step recognises its own binding key from the payload and passes
through anything else. Nothing routes by URL, path or domain.

## No registry requests here

Deliberate. The registry has no route through the gateway and publishes on
loopback only, so nothing in a shared collection could reach it.
`bin/setup.py` seeds all of it -- five participants and both capability
bindings -- from the same `.env` the adapter configs are rendered from, which is
what keeps the two from disagreeing.

To look at a registry row, tunnel to the VM and use `Participant/search`
directly; the quick-start README has the curl.

## networkAdapterUrl

Present as a variable, used by no request. Discover reaches the network adapter
through the experience adapter and publish through the provider adapter, so
nothing here calls it directly. It is there because it is the other adapter a
deployment exposes publicly: its `/publish` and `/discover` both verify
signatures, so a network peer calls it directly. Signing is not something
Postman does, so those calls are not scripted.
