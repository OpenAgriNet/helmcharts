# GFR registry and recommendation: full catalog

`gfr-catalog-publish.json` is the complete `cat-gfr` catalog: one `gfr-advice` resource per state
with GFR crops (31), each with its GFR crops, LGD code, SHC's own state id and Survey of India
boundary, plus the India-wide `gfr-soil-report` resource. The collection's GFR Publish request
carries a sample (Madhya Pradesh and the soil test).

It is built by `scripts/catalog-builders/gfr-registry-recommendation` in
`OpenAgriNet/network-adapter`; rebuild there and replace the file here. The network parts and
links are Postman variables the collection defines (`{{networkId}}`, `{{otherNetworkId}}`,
`{{bppId}}`, `{{bppUri}}`, `{{gfr-advisory-context}}`, `{{gfr-resource-context}}`,
`{{gfr-advice-operation-ref}}`, `{{gfr-soil-operation-ref}}` and the rest). To publish it, paste it
into the GFR Publish request and send it from there.

State codes, names and boundaries: Local Government Directory (LGD) and Survey of India. Crops:
SHC's crop registry, as of the build.
