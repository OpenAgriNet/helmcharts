# Mock providers

Three stand-in upstreams, so the stack can be exercised end to end without
credentials for the real services.

| mock | stands in for | port | auth |
|---|---|---|---|
| `mockimd` | IMD Mausamgram NWP | 9100 | basic |
| `mockagmarknet` | Agmarknet Vistaar | 9101 | token as a query parameter |
| `mockpmkisan` | PM-KISAN chatbot + grievance APIs | 9102 | a token in the body, one per service |

## Why the sources are here

The compose file **pulls** every image and builds nothing, so these are not
built by `make up`. They are here to be built and published once, and then
pulled like everything else:

    docker build -t ghcr.io/<org>/oan-mockimd:latest        mock-server/mockimd
    docker build -t ghcr.io/<org>/oan-mockagmarknet:latest  mock-server/mockagmarknet
    docker build -t ghcr.io/<org>/oan-mockpmkisan:latest    mock-server/mockpmkisan
    docker push ghcr.io/<org>/oan-mockimd:latest
    docker push ghcr.io/<org>/oan-mockagmarknet:latest
    docker push ghcr.io/<org>/oan-mockpmkisan:latest

Then set `MOCKIMD_IMAGE`, `MOCKAGMARKNET_IMAGE` and `MOCKPMKISAN_IMAGE` in
`.env`.

## What they deliberately get wrong

A mock that answered tidily would let a mapping pass here and fail against the
real service, so all three reproduce the awkward parts on purpose.

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

`mockpmkisan` serves **both** encryption envelopes the real service uses —
`aesCbcKeyInBand` for the OTP endpoints (the key travels beside the ciphertext
as 32 hex characters) and `aesGcm` for grievance — because the adapter picks
between them by capability and a mock speaking only one would leave half the
path untested. It spells its yes/no flag a **different way on every endpoint**
(`Rsponce`, `Responce`, `Status`, `status`), which is what the real service
does and what the mappings' fallback chain claims to handle; whichever
spelling a mapping forgot fails visibly instead of passing. Counts and handles
come back as the **wrong JSON type on purpose** — `LatestInstallmentPaid` as a
string, `GrievanceID` as a number — since the mappings claim to accept either.
It answers at `d.output` on some endpoints and bare `output` on others, as the
real one does. A missing or wrong token is answered with **plain text where the
envelope belongs**, which is how the real service refuses, so a passing run
proves the token was actually sent.

None of the three reproduces the real services' rate limits, downtime pages or
error bodies. What the real ones do on no-data, throttling or auth failure is
still unobserved.
