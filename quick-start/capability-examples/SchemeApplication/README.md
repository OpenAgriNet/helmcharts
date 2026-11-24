# SchemeApplication — PM-KISAN beneficiary status

Two calls sharing one `transactionId` (a shared `contract.id` reads better in
a log but is not required — the OTP is tied to the registration):

    init  ->  status

`init` sends an OTP to the registered mobile. `status` carries that OTP in
`challenge.response` and returns the beneficiary record in the same call — one
round-trip fewer than v1, which verified the OTP separately before fetching.

## Identifying the farmer

`commitmentAttributes` takes **either** `enrolmentId` **or**
`applicant.mobile`. These fixtures use the mobile. Supplying `enrolmentId`
instead makes the provider mapping look the farmer up by beneficiary id; the
rest of the envelope is unchanged.

## What a good answer looks like

`application.latestInstalmentPaid` arrives as a **number** (the upstream sends
a string; the mapping coerces it), `registeredOn` carries the `+05:30` offset,
and `blockers` lists what is holding up payment.

`blockers` **absent** and `blockers` **present but empty** are different
answers. Absent means the follow-up read failed (ADR-0005). Empty means
PM-KISAN checked and found nothing. Do not conflate them.

Commitment lifecycle: `DRAFT` on `init`, `ACTIVE` on `status`.

Verified 2026-11-20 against `mockpmkisan`, not against the real service.

## The `-aif` fixtures

The files ending `-aif` are Agriculture Infrastructure Fund, not PM-KISAN:
different provider, different pack version, different fields. They name
`v0.2`, which is **not published yet** — serve `network-specs` on the compose
network and rewrite the `@context` host before sending, as
`bharat-oan-api/migration-plan/aif/quick-try.md` describes.

AIF keys its session on the `transactionId`, so all of them must be sent in
order under one id: `init` (sends the OTP), `init` again carrying
`challenge.response.value`, then the two `status` calls. Out of order they are
refused for having no verified session.

Verified 2026-11-23 against `mock-aif`, not against the real service.
