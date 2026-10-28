# ADR-0003 — The stack starts in an order, and `down` means down

**Status:** Accepted
**Date:** 2026-10-28
**Issues:** OpenAgriNet/helmcharts#72

## Context

`quick-start` is a compose stack with optional profiles. Two things about it
are not obvious, and getting either wrong fails in a way that does not name its
cause.

## Decision

1. **The adapters start last, after their configs are rendered.** An adapter's
   config is a bind-mounted **file** that `setup.py` writes. Docker creates a
   **directory** at any bind-mount source that does not exist yet, so starting
   an adapter first both wedges that container on a directory it cannot parse
   and leaves a directory where the config has to be written. Recovering means
   removing paths under `config/adapters/` that look like they belong.
2. **Teardown names every profile.** Compose acts only on services whose
   profile is active, so a plain `docker compose down` leaves the gateway,
   observability and multi-adapter containers running and reports success.

`bin/stack.sh` is where both are encoded, which is why `make` delegates to it
rather than calling compose directly.

## Alternatives considered

- **Let compose's `depends_on` order it.** It orders container start, not the
  rendering of a file on the host, which is the step that has to come first.
- **Document the order and let people type compose commands.** The failure is
  silent and the recovery is `rm -rf` under a config directory. Encoding it
  once is cheaper than remembering it three times.

## Consequences

- `make up` stops at the containers; the adapters come up with
  `make configure-adapter`, which renders their configs first.
- Anyone running `docker compose` by hand bypasses both rules.
