# The front door. Every target is one line of delegation to bin/stack.sh, which
# is where the reasoning lives -- a Makefile is a bad place to explain why the
# startup order matters, and a good place to make the right order the shortest
# thing to type.
#
#     make up        the containers, in the order they have to start
#     make up-core   the same minus the reverse proxy and observability
#     make down      stop everything, keep the data
#     make help      the rest
#
# `make up` stops at the containers: the registry is empty and the adapters are
# not running. Filling it is four commands of its own, below.
#
# Anchored to this file's own directory rather than $(PWD), so `make -C
# quick-start up` works from the repo root.

STACK := $(dir $(realpath $(firstword $(MAKEFILE_LIST))))bin/stack.sh

.PHONY: help up up-core down destroy keys certs register-network register-provider configure-adapter host-descriptor admit refresh suspend verify reverse-proxy observability pull restart restart-edge ps logs

# Default target: running a bare `make` in a directory that can delete a
# Postgres volume should print the menu, not pick something from it.
help:
	@$(STACK) --help

# ---------------------------------------------------------------- the stack

up:            ; @$(STACK) up
up-core:       ; @$(STACK) up-core
down:          ; @$(STACK) down
destroy:       ; @$(STACK) destroy

# --------------------------------------------- the optional tiers, on their own
#
# `make up` already starts both. These are for starting one without the other,
# or restarting one after a config change.

reverse-proxy: ; @$(STACK) reverse-proxy
observability: ; @$(STACK) observability

# ------------------------------------------------ filling the registry
#
# In order: keys, register-network, register-provider PROVIDER=, then
# configure-adapter. One provider at a time keeps a wrong value to one provider.

keys:              ; @$(STACK) keys

# A certificate for this network's own name, signed by a CA shared by every
# network on this machine. `make up` issues one if there is none; this re-issues.
certs:             ; @$(STACK) certs
register-network:  ; @$(STACK) register-network

# PROVIDER is one of mausamgram, agmarknet, vistaar, pocra, pmkisan.
register-provider: ; @$(STACK) register-provider

# Renders from the registry's bindings, then starts the adapters -- together,
# because a rendered config nothing picked up did nothing.
#
# Asks for the mode and the crawler, writing both to .env. Answer up front with
# NETWORK_ADAPTER_DEPLOYMENT_MODE= and FEDERATION_CRAWL_PEERS=.
configure-adapter: ; @$(STACK) configure-adapter

# ------------------------------------------------------------------ the rest

# Generate and serve the federation documents. Re-run when the entry changes.
host-descriptor: ; @$(STACK) host-descriptor

# One direction at a time, and ours says nothing about theirs -- run it both
# ways for a pair. A network that has run on this machine needs no address:
# `make host-descriptor` recorded where it answers, and our CA signed it.
#
#   make admit PEER=net-b.oan.dev DIRECTION=source PACKS=all
admit:         ; @$(STACK) admit
refresh:       ; @$(STACK) refresh
suspend:       ; @$(STACK) suspend
verify:        ; @$(STACK) verify

# NPM chowns the files it is given, so a plain `git pull` fails on them.
pull:          ; @$(STACK) pull

# The app tier only -- registry, discovery, the three adapters. Not the
# databases, not the edge.
restart:       ; @$(STACK) restart
restart-edge:  ; @$(STACK) restart-edge
ps:            ; @$(STACK) ps

# `make logs` follows everything; `make logs SVC=registry` follows one service.
logs:          ; @$(STACK) logs $(SVC)
