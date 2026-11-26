# SATHI seed: full catalog

`sathi-catalog-publish.json` is the complete `cat-sathi` catalog: one `sathi-stock` resource per
Maharashtra district (36), each with SATHI's crop list, the district's LGD codes and its Survey
of India boundary. The collection's SATHI Publish request carries a one-district sample (Nashik).

It is built by `scripts/catalog-builders/sathi-seed` in `OpenAgriNet/network-adapter`; rebuild
there and replace the file here. The network parts and links are Postman variables the
collection defines (`{{networkId}}`, `{{otherNetworkId}}`, `{{bppId}}`, `{{bppUri}}`,
`{{sathi-resource-context}}`, `{{sathi-stock-operation-ref}}` and the rest). To publish it, paste
it into the SATHI Publish request and send it from there.

District codes, names and boundaries: Local Government Directory (LGD) and Survey of India.
Crops: SATHI's master API, as of the build.
