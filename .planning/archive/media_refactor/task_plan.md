# Media code refactor

## Goal

Merge genuinely shared movie/TV operations and split control-heavy functions
while preserving routes, database schema, stored data, and import behavior.

## Phases

- [complete] Measure functions, references, duplication, and current tests.
- [complete] Consolidate TMDB and image helpers.
- [complete] Consolidate shared model relations and split save orchestration.
- [complete] Simplify app orchestration and update tests.
- [complete] Run full regression, live route, and database checks.

## Boundaries

- Do not merge movie and TV semantics only for line-count reduction.
- Do not split functions solely because SQL or declarative dictionaries are long.
- Keep `app.py` free of direct requests and SQL.
- Do not change schema or production data.
- Preserve current public routes and form behavior.

## Errors encountered

- The global Python installation did not contain Flask; reran the suite with
  the project's `.venv` interpreter.
- One home-page assertion was stale after the existing navigation update; it
  now checks that the import form is absent from `/`, rather than requiring the
  navigation label to be absent.
