# Network of networks, locally.
#
# Runs a whole OAN network by name, as many times as you like on one machine --
# each with its own registry, its own discovery, its own adapter and a front
# door the others reach it at. Two of them is federation with nothing stubbed.
#
#     make network NETWORK_ID=bharatvistar.oan.local
#     make network NETWORK_ID=mahavistara.oan.local
#     make ls
#
# Self-contained: its own .env.example, registry schemas, setup and compose
# file. Nothing here reads or writes anything outside this directory.
#
# Anchored to this file's own directory rather than $(PWD), so
# `make -C networkofnetwork network ...` works from the repo root.

NET := $(dir $(realpath $(firstword $(MAKEFILE_LIST))))bin/network.sh

.PHONY: help network setup host-descriptor admit down destroy teardown ps logs ls

# Default target: running a bare `make` in a directory that can delete a
# Postgres volume should print the menu, not pick something from it.
help:
	@$(NET)

# ------------------------------------------------------------- one network

# Everything: ports, certificates, .env, containers, registry seed, front door.
# Safe to re-run -- it is also how you apply a config change.
network:  ; @$(NET) up      $(NETWORK_ID)

# Re-seed the registry and re-render the adapter config only.
setup:    ; @$(NET) setup   $(NETWORK_ID)

# Generate and sign the two documents this network HOSTS at its own domain --
# the descriptor and its jwks -- from its registry entry. Run it after
# `network`, and again whenever the entry changes.
#
# Named for the documents, not for the verb: "publish" on its own reads as
# publishing a CATALOG, which is a different thing entirely and goes to the
# adapter's /publish/ mount.
host-descriptor: ; @$(NET) host-descriptor $(NETWORK_ID)

# Record our decision to deal with another network: fetch its descriptor over
# TLS, verify the signature against the key it publishes, and store it.
#
# Two networks, so two variables. NETWORK_ID is US -- whose registry is written
# -- and PEER is them. It is one direction only; run it both ways for a pair.
#
#     make admit NETWORK_ID=bharatvistar.oan.local PEER=mahavistara.oan.local
admit:    ; @$(NET) admit   $(NETWORK_ID) $(PEER)

down:     ; @$(NET) down    $(NETWORK_ID)

# Deletes the data AND releases the ports, so the block can be reused.
destroy:  ; @$(NET) destroy $(NETWORK_ID)

# Destroy EVERY deployed network. Asks first; TEARDOWN_YES=1 skips the prompt.
#
#     make teardown
teardown: ; @$(NET) teardown

ps:       ; @$(NET) ps      $(NETWORK_ID)
logs:     ; @$(NET) logs    $(NETWORK_ID) $(SVC)

# ----------------------------------------------------------- every network

# What is deployed on this machine, and which ports each one holds.
ls:       ; @$(NET) ls
