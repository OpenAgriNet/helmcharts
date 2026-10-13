# Changelog

All notable changes to the quick-start stack are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-10-13

### Added
- **Single network adapter, now the default mode.** One adapter process runs
  the consumer, network and provider tiers as modules on one port:
  - service `network-adapter-layer`, container `network-adapter`, compose
    profile `network`;
  - default port `127.0.0.1:9200`, set with `NETWORK_ADAPTER_LAYER_PORT`;
  - paths `/<action>` (consumer), `/network/*`, `/provider/*`,
    `/provider/publish`;
  - config `config/adapters/network-adapter.yaml`, rendered by
    `bin/setup.py` from `network-adapter.yaml.tmpl`; it uses the same keys
    and registry rows as the three tier adapters.
- **Multi mode stays available as an option.** The three tier adapters
  (`provider-adapter` :9200, `network-adapter` :9201, `consumer-adapter`
  :9202) are now under compose profile `multi`.
- Make targets: `make up-multi` and `make up-core-multi` start multi mode;
  `make smoke` runs publish, discover and select through the running mode.
- `bin/stack.sh` takes `MODE=single|multi` (default `single`).
- `.env.example` gains `NETWORK_ADAPTER_LAYER_PORT=9200` and
  `COMPOSE_PROFILES=network`, so a plain `docker compose up -d` starts single
  mode.
- API collection for the single adapter:
  `api-collection/OpenAgriNet.network-adapter.api-collection.json`.

- `SETUP.md`: install and run steps, local and VM, for both modes. The
  README now covers the modes, how to switch, and the reference appendices.

### Changed
- `make up` and `make up-core` start single mode instead of the three tier
  adapters.
- Only one mode runs at a time, since both use port 9200. Switching mode
  removes the other mode's adapter containers.
- `make restart` restarts the adapters of whichever mode is running.

### Upgrade notes
- Re-run `python3 bin/setup.py` once to render `network-adapter.yaml`.
- To keep the old three-adapter layout, use `make up-multi`, or set
  `COMPOSE_PROFILES=multi` for plain compose commands.

## [0.1.0] - 2026-09-07

### Added
- Initial quick-start stack with Docker Compose:
  - registry (Sunbird RC) with its Postgres and Keycloak;
  - discovery service with its Postgres;
  - three adapters, one per tier: provider (port 9200), network (9201) and
    consumer (9202);
  - two mock upstreams standing in for Mausamgram and Agmarknet;
  - optional profiles `reverse-proxy` (nginx-proxy-manager) and
    `observability` (hyperdx).
- `bin/setup.py`: generates keys, seeds the registry and renders the adapter
  configs.
- `make up` starts the whole stack in order; `make up-core` starts it without
  the reverse proxy and observability.
