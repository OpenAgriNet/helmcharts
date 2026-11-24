# Mock providers

Seven stand-in upstreams, so the stack can be exercised end to end without
credentials for the real services.

Three are a convenience: the real service can be called, and in a normal
deployment is. `mockaif`, `mockpmfby`, `mockkcc` and `mockkhc` are not. The
first three verify a farmer with an OTP to their registered mobile number, so
nobody who is not that farmer can exercise the session path against the real
service at all. KCC goes further: the host it publishes is PRODUCTION, so an OTP
sent there is a real SMS to a real farmer's phone. KHC goes further in the other
direction: its filing WRITES a grievance to a real help desk, which no amount of
care makes repeatable, so the live host is for one deliberate test with the desk
owner and every other run comes here.

| mock | stands in for | port | auth |
|---|---|---|---|
| `mockimd` | IMD Mausamgram NWP | 9100 | basic |
| `mockagmarknet` | Agmarknet Vistaar | 9101 | token as a query parameter |
| `mockpmkisan` | PM-KISAN chatbot + grievance APIs | 9102 | a token in the body, one per service |
| `mockaif` | Agriculture Infrastructure Fund | 9103 | an `apiKey` header **and** a per-farmer bearer |
| `mockpmfby` | PMFBY insurance (NIC) + grievance (FGMS) | 9104 | a `token` header on NIC, a login-issued `Token` on FGMS |
| `mockkcc` | KCC loan application status | 9105 | a form login, then its `token` header, over sealed bodies |
| `mockkhc` | Kisan Help Center grievances + reference lists | 9106 | an `ApiKey` header **and**, on the filing, a per-farmer token |

## Why the sources are here

The compose file **pulls** every image and builds nothing, so these are not
built by `make up`. They are here to be built and published once, and then
pulled like everything else:

    docker build -t ghcr.io/<org>/oan-mockimd:latest        mock-server/mockimd
    docker build -t ghcr.io/<org>/oan-mockagmarknet:latest  mock-server/mockagmarknet
    docker build -t ghcr.io/<org>/oan-mockpmkisan:latest    mock-server/mockpmkisan
    docker build -t ghcr.io/<org>/oan-mockaif:latest        mock-server/mockaif
    docker build -t ghcr.io/<org>/oan-mockpmfby:latest      mock-server/mockpmfby
    docker build -t ghcr.io/<org>/oan-mockkcc:latest        mock-server/mockkcc
    docker build -t ghcr.io/<org>/oan-mockkhc:latest        mock-server/mockkhc
    docker push ghcr.io/<org>/oan-mockimd:latest
    docker push ghcr.io/<org>/oan-mockagmarknet:latest
    docker push ghcr.io/<org>/oan-mockpmkisan:latest
    docker push ghcr.io/<org>/oan-mockaif:latest
    docker push ghcr.io/<org>/oan-mockpmfby:latest
    docker push ghcr.io/<org>/oan-mockkcc:latest
    docker push ghcr.io/<org>/oan-mockkhc:latest

Then set `MOCKIMD_IMAGE`, `MOCKAGMARKNET_IMAGE`, `MOCKPMKISAN_IMAGE`,
`MOCKAIF_IMAGE`, `MOCKPMFBY_IMAGE`, `MOCKKCC_IMAGE` and `MOCKKHC_IMAGE` in
`.env`.

## What they deliberately get wrong

A mock that answered tidily would let a mapping pass here and fail against the
real service, so all six reproduce the awkward parts on purpose.

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

None of the six reproduces the real services' rate limits, downtime pages or
error bodies. What the real ones do on no-data, throttling or auth failure is
still unobserved.

`mockkcc` seals **only its successes**. A request body and a successful reply
are AES-256-CBC under a fixed key, written as hex under `data`; a refusal comes
back in clear as `{"status": false, "message": "..."}`. That asymmetry is the
only thing telling an answer from a refusal, because opening the envelope
replaces the whole body -- so a mapping reading the two the same way is caught
here. Its login is form-urlencoded and wants `entityType` beside the
credential, which the mock checks, so a config that drops the constant fails
now rather than later. `applicationStatus` answers the OTP and reads the
applications in ONE call, as KCC has it -- there is no verify endpoint, so a
wrong OTP is refused there. The OTP is checked against the `requestID` rather
than spent, so a retry asks the same question again. Which applications come
back is keyed off the mobile's last digit: `0` answers an EMPTY list (an
answer, not a refusal), `1` answers one, anything else answers three -- one of
which is REJECTED behind a `DRAFT` status, with only `rejectionReason` saying
so. `applicationNo` is a number on one row and a string on another, and amounts
arrive as numbers, as digit strings, and as null.

`mockkhc` answers with a status **string**, not a number: `"200 OK"` inside a
200 response, `"404 NOT_FOUND"` inside a 404. A caller that reads `status` as an
integer, or trusts the HTTP code alone, is caught here -- that is what the real
service does. Its reference lists come back **whole**: 36 states, 38 districts,
9 sub-districts, 86 villages, 37 schemes, 10 categories, 6 sub-categories, with
no paging to ask for, which is why paging is the tool's job and not a query
parameter. The parent goes out camelCase (`stateId`) and comes back lowercase
and unseparated (`stateid`), and the mock keeps that mismatch rather than
tidying it.

Village names **repeat across sub-districts**, so a caller that assumes one name
resolves to one village gets several and has to ask. Sub-category rows carry no
parent field, as the real ones do not, so the mock names the parent inside each
generic sub-category's own name -- the gap left visible rather than papered
over. Identifiers are derived (`district = state*100 + n` and so on down), which
is what lets the filing check that a `villageId` really sits under the `stateId`
sent; whether the real service makes that check is unobserved.

Two things about the status journey are reproduced because they are what blocks
it from being bound at all (open-issues item 20): `GET .../grievances/status`
opens a record on the grievance number alone, with **no OTP**, while
`POST .../check-status` reads its `grievanceNo` from the **query string** and
refuses it in the body -- and `POST .../verify-otp` is the other way round, body
only. A POST whose only input is a query parameter cannot be expressed by a
registry row.

The farmer's token on the filing goes on `X-KHC-Token`, which is **ours, not
KHC's** -- no operation declares a second security scheme and open item 1 in
`migration-plan/khc/plan.md` carries the question. The mock checks it so the
adapter is forced to send the token somewhere rather than drop it silently; the
day KHC answers, the name changes in one place. A mobile other than
`-registered-mobile` answers `isRegistered: false` and must then send a name and
a gender, so both branches of the filing are reachable without a second service.
Every place name outside scheme 38 and its ten categories is filler and must not
be read as KHC's master data.
