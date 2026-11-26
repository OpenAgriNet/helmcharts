# Full catalogs

The complete catalogs the catalog builders write, for publishing every district or state.
The builders are in `OpenAgriNet/network-adapter`, `scripts/catalog-builders/` (`sathi-seed`,
`gfr-registry-recommendation`); rebuild there and replace the files here.
The API collection's SATHI and GFR Publish requests carry one-district and one-state samples
of these.

| File | Catalog | Resources |
|---|---|---|
| `sathi-catalog-publish.json` | `cat-sathi` | 36 Maharashtra districts (`sathi-stock`) |
| `gfr-catalog-publish.json` | `cat-gfr` | 31 states with GFR crops (`gfr-advice`) and the India-wide soil test (`gfr-soil-report`) |

The network parts and the links are Postman variables the collection defines: `{{$guid}}`,
`{{$isoTimestamp}}`, `{{networkId}}`, `{{senderId}}`, `{{receiverId}}`, `{{bppId}}`, `{{bppUri}}`,
`{{otherNetworkId}}`, `{{sathi-resource-context}}`, `{{sathi-stock-operation-ref}}`,
`{{gfr-advisory-context}}`, `{{gfr-resource-context}}`, `{{gfr-advice-operation-ref}}` and
`{{gfr-soil-operation-ref}}`. To publish one, paste it into the matching Publish request and
send it from there.

District and state codes, names and boundaries: Local Government Directory (LGD) and Survey of
India. Crops: SATHI's master API and SHC's crop registry, as of the build.
