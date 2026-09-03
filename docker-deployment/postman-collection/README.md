# Postman collection

`OAN-dev-flow.postman_collection.json` runs the whole flow against a stack
brought up from the compose file beside it: a write token, the provider's two
registry rows, both registry searches, publish, discover and select.

Import it and set **one** variable — `upstreamBaseUrl`, a URL the VM can reach
for your upstream API. Everything else is prefilled in the collection itself,
including `providerId` and `capability`, which are set to the values this
deployment's `.env.example` uses. So the binding key the collection creates
already matches the adapter that will serve it.

Two things to know:

- **`bin/setup.py` must have run first.** It seeds the three adapter
  identities and renders their configs. The collection adds only the provider,
  because the provider's base URL is not the stack's to know.
- **Run the requests in order the first time.** Request 1 issues the token that
  requests 2 and 3 need. Re-running is safe: the registry is append-only, so a
  repeat create reports the existing row rather than changing anything.

The requests carry assertions, so a run tells you whether the stack is
actually healthy rather than just returning 200s. Among them: that the three
adapter identities exist with signing keys, that publish is `ACCEPTED`, that
`select` answers with a resource per forecast day, that its status is in the
spec's enum, and that every resource carries a `quantity`.

If ports are bound to loopback on the VM, tunnel first and the defaults work
unchanged:

    ssh -L 9202:127.0.0.1:9202 -L 9200:127.0.0.1:9200 \
        -L 8081:127.0.0.1:8081 -L 8080:127.0.0.1:8080 you@the-vm
