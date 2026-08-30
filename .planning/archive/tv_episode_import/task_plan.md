# TV episode import

## Goal

Add TMDB TV, season, and episode retrieval; normalized SQLite persistence; and
a manual one-video-per-episode import flow while keeping `app.py` concise.

## Phases

- [complete] Inspect current schema, application, template, and tests.
- [complete] Design TV/season/episode/source tables and migration.
- [complete] Add TMDB TV helpers and SQLite model operations.
- [complete] Extend the Flask workflow and media-type form/list UI.
- [complete] Back up and migrate the production database.
- [complete] Add regression tests and run local/live validation.

## Boundaries

- Preserve all existing movie tables, rows, routes, and import behavior.
- Store one AList source relationship per TV episode.
- Retain TMDB fields in normalized columns where practical; do not duplicate
  season or episode records when more sources are added.
- Keep external requests in `helpers.py`, SQLite in `models.py`, orchestration in
  `app.py`, and Bootstrap/Jinja presentation in templates.

## Errors encountered

- Two inline Python diagnostics were misquoted by PowerShell and failed before
  execution; replaced them with the SQLite CLI and a simpler double-quoted
  Python command.
- An inline in-memory migration validation was also parsed by PowerShell before
  Python; replaced it with a scoped validation script.
- A later inline baseline-table count hit the same PowerShell quoting issue;
  baseline validation will be moved into the existing scoped script.
- The first live-validation script could not import project modules because its
  directory replaced the project root on `sys.path`; inserted the verified root
  before importing and the next run passed.
- The first production backup was copied but source hashing was blocked by the
  running Flask process; created and verified a second transaction-consistent
  backup through SQLite's online backup command before migration.
- One post-migration inline HTML assertion was misquoted by PowerShell; the
  database checks completed and the UI check will use a simpler command.
- The migration validator initially recopied the now-migrated production DB on
  final QA, so migration 002 correctly rejected duplicate tables; pinned the
  validator to the verified pre-migration backup for repeatable checks.
