# registry migrations

Versioned SQL applied to the `registry` database, in Flyway naming order
(`V1__…`, `V2__…`).

**This directory is intentionally empty of migrations.** Sunbird RC core creates
its own tables from the entity definitions it is given, so the registry needs no
Flyway-managed schema today. The target exists so that anything OAN adds
alongside the registry — a view, an index, a lookup table — has an obvious home
and a versioned history.

A target whose directory contains no `.sql` files is skipped, and the Job logs
that it skipped it.
