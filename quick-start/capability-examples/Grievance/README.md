# Grievance — PM-KISAN

Send these in order against the consumer adapter
(`localhost:9201/consumer/<action>`), replacing `transactionId`, `messageId`
and `timestamp` each run. All three share one `transactionId`.

They do **not** have to share a `contract.id` — checked, and `status` is
answered with a fresh one. The OTP is tied to the registration, not to the
contract. Keep them the same anyway in anything you write by hand: it costs
nothing and it is what makes a log readable.

    init  ->  support  ->  status

`init` asks for the OTP. `support` lodges the complaint **and verifies the OTP
in the same call** — v1 needed a separate verify round-trip, v2 carries the
code in `challenge.response`. `status` reads the complaint back.

## The one manual step

`status.json` carries `case.filedOn` as the literal string
`REPLACE-WITH-THE-filedOn-support-RETURNED`. **Substitute the `filedOn` that
`support` actually returned before sending it.**

This is not tidiness. v2 identifies a grievance by its filing date, so a
fixture with a date baked in exercises the not-found path by accident and the
read path never runs. A green run on an unedited `status.json` means nothing.

## Shapes worth knowing before you edit these

- Every commitment needs `status.descriptor.code` — `DRAFT` on `init`,
  `ACTIVE` on `status`. Omitting it gives `SCH_REQUIRED_FIELD_MISSING`.
- `provider` belongs on `offer.provider`. Inside `commitmentAttributes` it is
  `SCH_FIELD_NOT_ALLOWED`.
- `support` is the exception: it uses `message.support.channels[0]`, not a
  contract, and *that* object carries `provider` at the top level.

## Categories

`grievance.category.code` is enumerated `G001`-`G010` by the v0.1 pack.
Anything outside that is refused with `SCH_INVALID_ENUM`. The BV v1 tool's own
twenty grievance types use `G011`-`G030` and are a different taxonomy — see
`bharat-oan-api/migration-plan/pmkisan/plan.md`.

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
