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

    docker build -t ghcr.io/<org>/oan-mockimd:latest       mocks/mockimd
    docker build -t ghcr.io/<org>/oan-mockagmarknet:latest  mocks/mockagmarknet
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
