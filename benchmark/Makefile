# The front door for the benchmark.
#
#     make                  the menu
#     make dependency-check is everything this needs installed?
#     make publish URL=...  run the publish benchmark
#     make discover URL=... run the discover benchmark
#
# The payloads are NOT committed -- 100,000 resources is about 130 MB and this
# repository is public. Build them once after a clone, which takes about twenty
# seconds:
#
#     make data
#
# They are deterministic, so `make verify-data` proves the result against the
# committed digests in data/CHECKSUMS.
#
# Anchored to this file's own directory rather than $(PWD), so `make -C
# benchmark publish` works from the repository root.

HERE := $(dir $(realpath $(firstword $(MAKEFILE_LIST))))
BENCH := $(HERE)tools/run-benchmark.sh

# What to drive, and how hard. Override any of these on the command line:
#
#     make discover URL=http://host:8080 THREADS=40 DURATION=900
#
URL        ?=
CAPABILITY ?= MandiPrice

# Lowercased, because the configs and payloads directories are named that way:
# config/<slug>-publish.yaml, data/<slug>-discover, and so on.
SLUG := $(shell echo $(CAPABILITY) | tr '[:upper:]' '[:lower:]')
THREADS    ?= 10
RAMP_UP    ?= 30
DURATION   ?= 300
RATE       ?= 0

# Iterations per thread. -1 means keep looping and let DURATION stop the run.
# A positive number switches to iteration-count mode: every thread finishes its
# loops however long that takes, and DURATION no longer applies.
LOOPS ?= -1

STARTUP_DELAY    ?= 0
ON_SAMPLE_ERROR  ?= continue
CONNECT_TIMEOUT  ?= 10000
RESPONSE_TIMEOUT ?= 120000

# What each service was given. Labels, not settings: you dial the real limits in
# Helm, and these record what you dialled so a run says what it measured.
#
# The deployment has three adapters with different jobs, so which limits matter
# depends on the scenario:
#
#   publish    consumer -> network -> discovery -> discovery's database
#   discover   consumer -> network -> discovery -> discovery's database
#   select     consumer -> network -> provider  -> the upstream provider
#
# Every adapter also reads the registry to verify the caller, so the registry
# and its database are in the path of all three.
#
# Leave unset whatever a scenario does not touch; only what is set reaches the
# report.
CONSUMER_CPU  ?=
CONSUMER_MEM  ?=
NETWORK_CPU   ?=
NETWORK_MEM   ?=
PROVIDER_CPU  ?=
PROVIDER_MEM  ?=
DISCOVERY_CPU ?=
DISCOVERY_MEM ?=
DISCOVERY_DB_CPU ?=
DISCOVERY_DB_MEM ?=
REGISTRY_CPU  ?=
REGISTRY_MEM  ?=
REGISTRY_DB_CPU ?=
REGISTRY_DB_MEM ?=

# A short handle for the whole configuration, used in the run directory name.
# Ten limits will not fit in a directory name; this is what distinguishes runs
# at a glance, and the report carries the detail.
CONFIG ?= na

# How often JMeter prints a running summary during a run.
PROGRESS_INTERVAL ?= 300

# How often CPU and memory are sampled.
SAMPLE_INTERVAL ?= 5

# Where run directories are written.
OUT ?=

# Where the CPU and memory samples come from: k8s, docker, or none.
SAMPLE    ?= none
NAMESPACE ?=
SELECTOR  ?=
NAMES     ?=

# Optional: measured before the run, so a reader can tell a slow service from a
# distant one.
HEALTH ?=

# A line describing this run, kept in the report. For select especially:
# whether the adapter was pointed at a mock provider or the real one.
NOTE ?=

# Extra arguments passed straight through, e.g. ARGS="--record-host"
ARGS ?=

BENCH_ARGS = --capability $(CAPABILITY) --url $(URL) \
             --threads $(THREADS) --ramp-up $(RAMP_UP) --duration $(DURATION) \
             --rate-per-min $(RATE) --loops $(LOOPS) \
             --startup-delay $(STARTUP_DELAY) --on-sample-error $(ON_SAMPLE_ERROR) \
             --connect-timeout $(CONNECT_TIMEOUT) --response-timeout $(RESPONSE_TIMEOUT) \
             --config $(CONFIG) \
             $(if $(CONSUMER_CPU),--limit consumer-adapter=$(CONSUMER_CPU)/$(CONSUMER_MEM)) \
             $(if $(NETWORK_CPU),--limit network-adapter=$(NETWORK_CPU)/$(NETWORK_MEM)) \
             $(if $(PROVIDER_CPU),--limit provider-adapter=$(PROVIDER_CPU)/$(PROVIDER_MEM)) \
             $(if $(DISCOVERY_CPU),--limit discovery-service=$(DISCOVERY_CPU)/$(DISCOVERY_MEM)) \
             $(if $(DISCOVERY_DB_CPU),--limit discovery-postgres=$(DISCOVERY_DB_CPU)/$(DISCOVERY_DB_MEM)) \
             $(if $(REGISTRY_CPU),--limit registry-service=$(REGISTRY_CPU)/$(REGISTRY_MEM)) \
             $(if $(REGISTRY_DB_CPU),--limit registry-postgres=$(REGISTRY_DB_CPU)/$(REGISTRY_DB_MEM)) \
             --sample-source $(SAMPLE) --progress-interval $(PROGRESS_INTERVAL) \
             --sample-interval $(SAMPLE_INTERVAL) \
             $(if $(OUT),--out $(OUT)) \
             $(if $(NAMESPACE),--namespace $(NAMESPACE)) \
             $(if $(SELECTOR),--selector $(SELECTOR)) \
             $(if $(NAMES),--names $(NAMES)) \
             $(if $(HEALTH),--health-path $(HEALTH)) \
             $(if $(NOTE),--note "$(NOTE)") \
             $(ARGS)

.PHONY: help dependency-check publish discover select data publish-data discover-data select-data get-mandi-metadata verify-data checksums results report fetch-results clean clean-results

# Default target: a bare `make` in a directory that can start a ten-minute load
# test should print the menu, not pick something from it.
help:
	@echo "Benchmark"
	@echo
	@echo "  make dependency-check               is everything installed?"
	@echo "  make publish  URL=http://host:port  run the publish benchmark"
	@echo "  make discover URL=http://host:port  run the discover benchmark"
	@echo "  make select   URL=http://host:port  run the select benchmark"
	@echo
	@echo "  make data                           build payloads, queries, selects"
	@echo "  make verify-data                    rebuild and confirm nothing changed"
	@echo "  make checksums                      re-record data/CHECKSUMS after a change"
	@echo "  make get-mandi-metadata             refetch mandi metadata (needs credentials)"
	@echo "  make results                        list the runs recorded so far"
	@echo "  make report                         print the newest report (or RUN=<dir>)"
	@echo "  make fetch-results REMOTE=...       copy runs back from the load machine"
	@echo "  make clean                          clear the Go build cache"
	@echo "  make clean-results                  delete recorded runs (asks first)"
	@echo
	@echo "Settings, overridable on the command line:"
	@echo "  URL CAPABILITY THREADS RAMP_UP DURATION RATE LOOPS"
	@echo "  STARTUP_DELAY ON_SAMPLE_ERROR CONNECT_TIMEOUT RESPONSE_TIMEOUT"
	@echo "  CAPABILITY          which capabilities/<slug>/ to use (default MandiPrice)"
	@echo "  CONFIG              short handle for the run directory name"
	@echo "  CONSUMER_CPU/_MEM NETWORK_CPU/_MEM   the adapters"
	@echo "  PROVIDER_CPU/_MEM                    the provider adapter (select)"
	@echo "  DISCOVERY_CPU/_MEM DISCOVERY_DB_CPU/_MEM"
	@echo "  REGISTRY_CPU/_MEM  REGISTRY_DB_CPU/_MEM"
	@echo "  SAMPLE              k8s | docker | none"
	@echo "  NAMESPACE SELECTOR  which pods to sample     (SAMPLE=k8s)"
	@echo "  NAMES               which containers to sample (SAMPLE=docker)"
	@echo "  HEALTH              path to time as a baseline, e.g. /healthz"
	@echo "  PROGRESS_INTERVAL   seconds between progress lines (default 300)"
	@echo "  SAMPLE_INTERVAL     seconds between CPU/memory samples (default 5)"
	@echo "  OUT                 where run directories go (default ./results)"
	@echo "  NOTE                a line kept in the report, e.g. NOTE='mock provider'"
	@echo "  STATES FROM_DATE TO_DATE   for make get-mandi-metadata"
	@echo
	@echo "  make discover URL=http://host:8080 THREADS=40 DURATION=900 \\"
	@echo "       CONFIG=all-1cpu NETWORK_CPU=1 NETWORK_MEM=1Gi \\"
	@echo "       DISCOVERY_CPU=1 DISCOVERY_MEM=1Gi SAMPLE=k8s NAMESPACE=oan"

# ------------------------------------------------------------------ running

publish:
	@$(if $(URL),,$(error URL is required, e.g. make publish URL=http://host:8080))
	@$(BENCH) --scenario publish $(BENCH_ARGS)

discover:
	@$(if $(URL),,$(error URL is required, e.g. make discover URL=http://host:8080))
	@$(BENCH) --scenario discover $(BENCH_ARGS)

# Calls a provider, so what it measures depends on which provider the ADAPTER is
# configured to call — a mock or the real one. JMeter cannot tell the difference
# and does not need to; record which it was with NOTE= so the report says so.
select:
	@$(if $(URL),,$(error URL is required, e.g. make select URL=http://host:8080))
	@$(BENCH) --scenario select $(BENCH_ARGS)

# ------------------------------------------------------------------- checks

# Run this before a long test rather than after it. A ten-minute run that
# cannot write a report has wasted ten minutes.
dependency-check:
	@echo "go       $$(command -v go >/dev/null && go version || echo 'NOT FOUND')"
	@# NOT `head -1`: jmeter leads with a log4j plugin-scanning warning on some
	@# installs, and a banner on others, so the first line is rarely the version.
	@# Same trap already fixed in tools/run-benchmark.sh.
	@echo "jmeter   $$(command -v jmeter >/dev/null && (jmeter --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | tail -1) || echo 'NOT FOUND')"
	@echo "kubectl  $$(command -v kubectl >/dev/null && echo present || echo 'not found (only needed for SAMPLE=k8s)')"
	@echo "docker   $$(command -v docker >/dev/null && echo present || echo 'not found (only needed for SAMPLE=docker)')"
	@echo "curl     $$(command -v curl >/dev/null && echo present || echo 'not found (only needed for HEALTH=)')"
	@echo
	@echo "payloads $$(ls $(HERE)data/*-payload/*/files.csv 2>/dev/null | wc -l) set(s) prepared"

# ------------------------------------------------------------------- data

data: publish-data discover-data select-data

publish-data:
	@cd $(HERE) && go run ./capabilities/$(SLUG)/tools/prepare-publish-data --config capabilities/$(SLUG)/config/publish.yaml

# Both read the published payloads, so they have to run after it.
discover-data: publish-data
	@cd $(HERE) && go run ./capabilities/$(SLUG)/tools/prepare-discover-data --config capabilities/$(SLUG)/config/discover.yaml

select-data: publish-data
	@cd $(HERE) && go run ./capabilities/$(SLUG)/tools/prepare-select-data --config capabilities/$(SLUG)/config/select.yaml

# All three generators are deterministic, so rebuilding without changing
# anything must produce the same bytes. The payloads themselves are too large to
# commit, so what is compared is data/CHECKSUMS -- a digest per generated file.
#
# A mismatch means something upstream moved, which is worth seeing now rather
# than discovering halfway through a benchmark.
verify-data: data
	@cd $(HERE) && ./tools/checksums.sh verify

# Re-record the digests. Run it after deliberately changing a config, a template
# or the metadata, and commit data/CHECKSUMS with that change.
checksums: data
	@cd $(HERE) && ./tools/checksums.sh write

# Which states the metadata covers.
#
# EMPTY MEANS ALL OF THEM, which is what the 100,000-resource target needs:
# five states hold about 10k real market-commodity pairs and all of India should
# hold roughly 50k. Narrow it when a smaller set is wanted:
#
#     make get-mandi-metadata STATES='Karnataka,Kerala,Tamil Nadu'
#
# The catalogs follow whatever is fetched -- publish.yaml lists no entries, so
# one catalog is derived per state present in the metadata.
STATES ?=

# The window the market-commodity mapping is asked about. A market with no
# trades recorded in it carries no commodities, and is skipped when the payloads
# is built.
FROM_DATE ?= 01-01-2026
TO_DATE   ?= 01-12-2026

# Needs credentials, and is not part of a benchmark run. The market, district
# and commodity data changes rarely, which is why the result is committed.
#
# Agmarknet-specific, and named so: it is the only target here tied to one
# provider. Another capability's metadata comes from somewhere else entirely and
# would get its own target rather than a flag on this one.
get-mandi-metadata:
	@$(if $(AGMARKNET_MASTER_URL),,$(error set AGMARKNET_TOKEN_URL, AGMARKNET_MASTER_URL, AGMARKNET_MAPPING_URL, AGMARKNET_ACCESS_NAME and AGMARKNET_PASSWORD in the environment first))
	@cd $(HERE) && go run ./capabilities/mandiprice/tools/fetch-metadata \
		--out data/mandiPrice-metadata --states '$(STATES)' \
		--from-date '$(FROM_DATE)' --to-date '$(TO_DATE)'

# ------------------------------------------------------------------- other

# ------------------------------------------------------- bringing runs home
#
# The load generator usually runs on a different machine from the one you read
# results on. This copies whole run directories back -- results.jtl included,
# because that is the evidence; report.md is only arithmetic done on it.
#
#     make fetch-results REMOTE=user@host:/path/to/benchmark/results
#
# Runs are named by timestamp, so nothing collides and re-running is safe: only
# what is missing comes across.
REMOTE ?=

fetch-results:
	@$(if $(REMOTE),,$(error REMOTE is required, e.g. REMOTE=user@host:/path/to/benchmark/results))
	@command -v rsync >/dev/null || { echo "rsync is not installed" >&2; exit 1; }
	@mkdir -p $(HERE)results
	@rsync -avh --progress "$(REMOTE)/" "$(HERE)results/"
	@echo
	@echo "pulled into $(HERE)results — now:  make results"

# ----------------------------------------------------------------- results

# One line per run: when, what was driven, and how it went. Read straight out
# of each report, so this cannot drift from what the reports say.
results:
	@if [ ! -d $(HERE)results ] || [ -z "$$(ls -A $(HERE)results 2>/dev/null | grep -v .gitkeep)" ]; then \
		echo "no runs recorded yet"; \
	else \
		for d in $(HERE)results/*/; do \
			[ -f "$$d/report.md" ] || continue; \
			name=$$(basename $$d); \
			reqs=$$(sed -n 's/^| Requests | \(.*\) |$$/\1/p' "$$d/report.md"); \
			tput=$$(sed -n 's/^| Throughput | \(.*\) |$$/\1/p' "$$d/report.md"); \
			err=$$(sed -n 's/^| Error rate | \(.*\) |$$/\1/p' "$$d/report.md"); \
			p95=$$(sed -n 's/^| Response time p95 | \(.*\) |$$/\1/p' "$$d/report.md"); \
			printf '%-52s %8s reqs  %14s  p95 %-9s err %s\n' "$$name" "$$reqs" "$$tput" "$$p95" "$$err"; \
		done; \
	fi

# The newest report, or a named one: make report RUN=20260921-...-publish
RUN ?=
report:
	@if [ -n "$(RUN)" ]; then \
		cat $(HERE)results/$(RUN)/report.md; \
	else \
		latest=$$(ls -1dt $(HERE)results/*/ 2>/dev/null | head -1); \
		if [ -z "$$latest" ]; then echo "no runs recorded yet"; else cat "$$latest/report.md"; fi; \
	fi

# Go's build and module caches, which is all there is to clean.
#
# NOT results: a benchmark run cannot be reproduced. The cluster it measured has
# moved on, so a results directory is a record of something that happened, not
# an artefact that can be rebuilt. NOT data/ either -- those payloads are
# committed source.
#
# A `clean` that quietly deletes either is how a comparison against last week's
# numbers stops being possible.
clean:
	@cd $(HERE) && go clean -cache -testcache 2>/dev/null || true
	@echo "cleared the Go build cache"
	@echo "kept results/ and data/ — neither can be rebuilt by this Makefile"

# Deliberately separate, and it asks. Typing the word rather than pressing y
# because this is the one action here with nothing behind it.
clean-results:
	@echo "This deletes every recorded benchmark run:"
	@echo
	@ls -1 $(HERE)results 2>/dev/null | sed 's/^/    /' || true
	@test -d $(HERE)results || echo "    (none)"
	@echo
	@echo "A run cannot be reproduced: the cluster it measured has moved on."
	@printf "Type 'delete' to confirm: "
	@read answer; \
	if [ "$$answer" = "delete" ]; then \
		rm -rf $(HERE)results; \
		echo "removed results/"; \
	else \
		echo "kept results/"; \
	fi
