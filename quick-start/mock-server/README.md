# Mock providers

Two stand-in upstreams, so the stack can be exercised end to end without
credentials for the real services.

| mock | stands in for | port | auth |
|---|---|---|---|
| `mockimd` | IMD Mausamgram NWP | 9100 | basic |
| `mockagmarknet` | Agmarknet Vistaar | 9101 | token as a query parameter |

## Why the sources are here

The compose file **pulls** every image and builds nothing, so these are not
built by `make up`. They are here to be built and published once, and then
pulled like everything else:

    docker build -t ghcr.io/<org>/oan-mockimd:latest       mock-server/mockimd
    docker build -t ghcr.io/<org>/oan-mockagmarknet:latest  mock-server/mockagmarknet
    docker push ghcr.io/<org>/oan-mockimd:latest
    docker push ghcr.io/<org>/oan-mockagmarknet:latest

Then set `MOCKIMD_IMAGE` and `MOCKAGMARKNET_IMAGE` in `.env`.

## What they deliberately get wrong

A mock that answered tidily would let a mapping pass here and fail against the
real service, so both reproduce the awkward parts on purpose.

`mockimd` answers `fcstday1..N` with the count coming from `-days`, so a mapping
that hardcodes five days is caught. Forecasts derive from the requested point,
so a wrong lat/lon shows up as wrong numbers rather than passing silently.

`mockagmarknet` answers a **bare JSON array** whose records use **Title Case
keys containing spaces** — `Modal Price`, `Arrival Date` — with **prices as
strings** and dates as `dd-MM-yyyy`. It requires the token as a query
parameter and answers 401 without it, which is what proves the adapter sent
one. Its last record reports no minimum or maximum, as the real data
sometimes does, so a mapping is forced to distinguish "not reported" from
"zero". Prices derive from the requested market and commodity codes, so a
wrong code is visible in the answer.

Neither reproduces the real services' error bodies or credentials. What the
real ones do on no-data, rate limits or auth failure is still unobserved.

## Experience app and DSS stand-ins

Two small Python programs, so the experience adapter and the DSS adapter can be
tried end to end without the real experience app or DSS. They run on your
machine, not in the stack, need no packages, and no make command starts them.
They are for a demo, not a test of the real services.

Before you start, the stack and both adapters must be running (see
[SetUp-EXPERIENCE-AND-DSS-ADAPTERS.md](../SetUp-EXPERIENCE-AND-DSS-ADAPTERS.md)).
The weather provider must be registered, with your token in `MAUSAMGRAM_AUTH`,
and its catalogue published. Publish it with the request "WeatherObservation
Capability - MausamGram / Publish - On-demand" in
[api-collection](../../api-collection/OpenAgriNet.api-collection.json). A
catalogue past its validity end date is not found by discover.

In one terminal, the DSS stand-in. It listens on port 8077, where the DSS
adapter expects the DSS (`DSS_URL`):

    DSS_NETWORK_SENDER_ID=<your DSS_SUBSCRIBER_ID> \
    DSS_NETWORK_HOST_ID=<your BECKN_NETWORK_ID> \
    python3 mock-server/dss-stub/dss_stub.py

In another, the experience app stand-in. It listens on port 8070.
`EXPERIENCE_ADAPTER_URL` is your experience adapter, where your real experience
app would send turns:

    EXPERIENCE_SENDER_ID=<your EXPERIENCE_SUBSCRIBER_ID> \
    EXPERIENCE_ADAPTER_URL=http://localhost:9203/dss \
    python3 mock-server/experience-stub/experience_stub.py

Then ask the experience app a question. The answer is an event stream, so use
`curl -N`:

    curl -N localhost:8070/ask -d '{"question": "Will it rain this week?"}'

Add `-H 'Accept: application/json'` to get one JSON body instead. The question
goes to the experience adapter, then the DSS adapter, then the DSS stand-in, and
the answer comes back the same way. Each stand-in logs its hop.

The DSS stand-in answers every turn the same way: a discover and a select for
weather at one point, both sent through the DSS adapter as the DSS. If the
network has nothing, or the provider fails, the outcome says `no_match` or
`unavailable`. It sends the stream in one piece, not as it is written. The
experience app stand-in passes the stream on as it arrives.
