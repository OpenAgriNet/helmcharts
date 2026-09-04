# The front door. Every target is one line of delegation to bin/stack.sh, which
# is where the reasoning lives -- a Makefile is a bad place to explain why the
# startup order matters, and a good place to make the right order the shortest
# thing to type.
#
#     make up        the whole stack, in the order it has to start
#     make up-core   the same minus the gateway and hyperdx
#     make down      stop everything, keep the data
#     make help      the rest
#
# Anchored to this file's own directory rather than $(PWD), so `make -C
# docker-deployment up` works from the repo root.

STACK := $(dir $(realpath $(firstword $(MAKEFILE_LIST))))bin/stack.sh

.PHONY: help up up-core down destroy setup gateway observability restart restart-edge ps logs

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

gateway:       ; @$(STACK) gateway
observability: ; @$(STACK) observability

# ------------------------------------------------------------------ the rest

setup:         ; @$(STACK) setup

# The app tier only -- registry, discovery, the three adapters. Not keycloak,
# not the databases, not the edge.
restart:       ; @$(STACK) restart
restart-edge:  ; @$(STACK) restart-edge
ps:            ; @$(STACK) ps

# `make logs` follows everything; `make logs SVC=registry` follows one service.
logs:          ; @$(STACK) logs $(SVC)
