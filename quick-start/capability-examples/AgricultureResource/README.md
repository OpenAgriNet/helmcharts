# AgricultureResource — SATHI seed stock

Send these in order: `publish/` to `localhost:9201/publish`, then `discover/`
and `select/` to `localhost:9201/consumer/<action>`, replacing
`transactionId`, `messageId` and `timestamp` each run. `discover` and `select`
share one `transactionId`.

    publish  ->  discover  ->  select

## Before you send them

- `make register-provider PROVIDER=sathi`, with the `SATHI_*` values from
  `.env.example`. The adapter image needs the `AgricultureResource` step.
- Extended (L2) schema validation off on the adapter, for now: the select
  carries SATHI's own fields, which the AgricultureResource pack does not
  declare.
- Select calls SATHI live, so it needs egress and `SATHI_API_KEY`.

## What the sample publishes

Nashik only, with three crops and a rough rectangle for its boundary.
**Publishing it replaces any `cat-sathi` already published.** A real catalog has
one resource per district, every SATHI crop, and the district's real boundary.

## Shapes worth knowing before you edit these

- **Discover uses `networkId: "*"`.** The catalog is stored as public; with this
  network's id the answer is empty.
- **Discover matches `descriptor.code == "sathi-stock"`.** Every SATHI resource
  carries that code, whatever its district.
- **Large districts match by bounding box** in the discovery service, so a point
  near a border can return two districts.
- **Select carries SATHI's own request fields** (`cropCode`, `seedClass`,
  `stateCode`, `districtCode`) in `resourceAttributes`, beside the
  AgricultureResource `@context` and `@type`. `@type` is required: the adapter
  binds on it.
- **Never send `apiKey`.** The adapter adds it from `SATHI_API_KEY`
  (`authScheme: body`).
- **`offer.provider.descriptor` is required** by the core schema.

## What comes back

`on_select` carries SATHI's own reply in `resourceAttributes`: `statusCode`,
`status`, `message` and `data` (one entry per seed lot, with the dealers holding
it). No stock is `statusCode` 409 with `message` "Data Not Found" and an empty
`data`, delivered as a normal answer, not an error.
