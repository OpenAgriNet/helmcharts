# keycloak migrations

Versioned SQL applied to the `keycloak` database.

**This directory is intentionally empty of migrations.** Keycloak manages its own
schema with Liquibase on startup, and a second migration tool writing the same
tables would fight it. Do not add table DDL for Keycloak's own tables here.

The target exists only so the database appears in this chart's inventory. A
target whose directory contains no `.sql` files is skipped.
