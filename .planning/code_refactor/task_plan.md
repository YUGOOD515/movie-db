# Application responsibility refactor

## Goal

Reduce `app.py` to Flask configuration and route orchestration while moving all
AList/TMDB/network and generic file/URL helpers to `helpers.py`, and SQLite model
operations to a dedicated `models.py`, without changing user-visible behavior.

## Phases

- [complete] Inspect current responsibilities, tests, and project instructions.
- [complete] Consolidate external-service and utility functions in `helpers.py`.
- [complete] Extract SQLite persistence and mapping queries into `models.py`.
- [complete] Simplify `app.py` and update tests for the new module boundaries.
- [complete] Run regression, live AList validation, database integrity, and code-size checks.

## Boundaries

- Preserve route behavior, database schema, stored data, and Bootstrap templates.
- Keep signed AList URLs out of the database.
- Do not modify unrelated user files or dependency versions.

## Errors encountered

- The planning skill's documented session-catchup script is not installed at
  the expected `.opencode` path; current files are used as source of truth.
