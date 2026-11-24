# Mock providers

Five stand-in upstreams, so the stack can be exercised end to end without
credentials for the real services.

Three are a convenience: the real service can be called, and in a normal
deployment is. `mockaif` and `mockpmfby` are not. Both verify a farmer with an
OTP to their registered mobile number, so nobody who is not that farmer can
exercise the session path against the real service at all.

| mock | stands in for | port | auth |
|---|---|---|---|
| `mockimd` | IMD Mausamgram NWP | 9100 | basic |
| `mockagmarknet` | Agmarknet Vistaar | 9101 | token as a query parameter |
| `mockpmkisan` | PM-KISAN chatbot + grievance APIs | 9102 | a token in the body, one per service |
| `mockaif` | Agriculture Infrastructure Fund | 9103 | an `apiKey` header **and** a per-farmer bearer |
| `mockpmfby` | PMFBY insurance (NIC) + grievance (FGMS) | 9104 | a `token` header on NIC, a login-issued `Token` on FGMS |

## Why the sources are here

The compose file **pulls** every image and builds nothing, so these are not
built by `make up`. They are here to be built and published once, and then
pulled like everything else:

    docker build -t ghcr.io/<org>/oan-mockimd:latest        mock-server/mockimd
    docker build -t ghcr.io/<org>/oan-mockagmarknet:latest  mock-server/mockagmarknet
    docker build -t ghcr.io/<org>/oan-mockpmkisan:latest    mock-server/mockpmkisan
    docker build -t ghcr.io/<org>/oan-mockaif:latest        mock-server/mockaif
    docker build -t ghcr.io/<org>/oan-mockpmfby:latest      mock-server/mockpmfby
    docker push ghcr.io/<org>/oan-mockimd:latest
    docker push ghcr.io/<org>/oan-mockagmarknet:latest
    docker push ghcr.io/<org>/oan-mockpmkisan:latest
    docker push ghcr.io/<org>/oan-mockaif:latest
    docker push ghcr.io/<org>/oan-mockpmfby:latest

Then set `MOCKIMD_IMAGE`, `MOCKAGMARKNET_IMAGE`, `MOCKPMKISAN_IMAGE`,
`MOCKAIF_IMAGE` and `MOCKPMFBY_IMAGE` in `.env`.

## What they deliberately get wrong

A mock that answered tidily would let a mapping pass here and fail against the
real service, so all five reproduce the awkward parts on purpose.

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

`mockaif` spells its flag `Success` and its text `Message` on every happy path
and `success`/`message` on the one BV42201 refusal, as the real one does, so a
mapping reading a single casing is caught. `/Status` answers a **bare JSON
string** (`"Disbursed"`), and `/support-tickets` answers a bare string when
there are no tickets and an array when there are — so a mapping that always
iterates, or always reads a field, fails visibly. The OTP is **single-use**:
verifying twice with the same one is refused, which is what forces the adapter
to hold the session rather than re-verify per call. Both credentials are
checked on every read, and a bearer issued for one beneficiary cannot be used
to ask about another, so a passing run proves the apiKey AND the farmer's
consent were both sent.

An even `loanApplicationNumber` has no loan and an even `beneficiaryId` has no
tickets, so the empty and not-found paths are reachable without a second magic
constant.

`mockpmfby` answers in **two different envelopes**, as PMFBY does: the NIC side
answers `{status, data, error}` with `status` a boolean, and the FGMS side
answers `{responseCode, responseMessage, responseDynamic}` with `responseCode`
the string `"1"` or `"0"`. A mapping reading one shape on both is caught. The
policy read nests its rows in `applicationList` inside each policy while the
claim read keeps them flat, so the two reads cannot share a mapping. Claim rows
mix casing on sibling keys (`Status` beside `ClaimStatus` beside
`claimPaymentStatus`) and carry nulls in numeric fields. Both reads return the
farmer's bank account number and Aadhaar-linked payment flag **on purpose** —
the mappings are supposed to drop them, and a mock that never sent them could
not tell a mapping that drops them from one that never saw them. The OTP is
single-use, which is what forces the adapter to hold the session rather than
re-verify per call.

A `farmerID` ending in `0` has no policies and no claims, so the empty-answer
path is reachable; `9999999999` is the mobile PMFBY does not know.

**One place `mockpmfby` does not match PMFBY.** Its `verifyMobile` answers
`data.farmerID` beside `data.verified`. The real one answers only
`{"verified": true}` and resolves a mobile to a `farmerID` through a separate
internal call, `farmerMobileExists`, which is not on the published Beckn
surface — and one Beckn action maps to one upstream call. So a mapping that
passes here will **not** pass against real PMFBY unmodified. `farmerMobileExists`
is served here too, with the real shape, so whichever way that gets closed has
something to test against.

None of the five reproduces the real services' rate limits, downtime pages or
error bodies. What the real ones do on no-data, throttling or auth failure is
still unobserved.
