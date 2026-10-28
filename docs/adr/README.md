# Architecture Decision Records

One file per decision, numbered, never edited after acceptance. A decision
that turns out wrong is **superseded** by a new record, not rewritten. The
point of the log is to answer *why does this look the way it does*, and a
rewritten record cannot answer it.

Start from [`0000-template.md`](0000-template.md).

| # | Decision | Issues |
|---|---|---|
| [0001](0001-single-network-adapter-deployment-mode.md) | One Beckn OAN network adapter process for all three tiers, by default (`single`); three adapters stay optional (`multi`) | helmcharts#55, #66 |
| [0002](0002-filling-the-registry.md) | Filling the registry: create-only rows, and what that costs | helmcharts#72 |
| [0003](0003-bringing-the-stack-up.md) | The stack starts in an order, and `down` means down | helmcharts#72 |
