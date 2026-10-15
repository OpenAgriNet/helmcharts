#!/bin/sh
# The crawler keeps its bookkeeping -- queues, cursors, staged catalogs -- in a
# database of its own on the discovery server.
#
# Created here, by postgres' own init hook, rather than by hand after the fact.
# Destroying a stack takes the volume with it, and without this the adapter
# restart-loops on "applying 0001_init.sql" against a database that is not there.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
	CREATE DATABASE catalogcrawler;
EOSQL
