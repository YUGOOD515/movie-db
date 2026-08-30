# IMDb-primary code cleanup

## Goal

Make the application natively use the seven-table `movie.db` plus read-only
`imdb.db`, removing transitional legacy-schema and OMDb code while preserving
all current library, detail, import, playback, artwork, favorite, and progress
behavior.

## Phases

- [complete] Establish a behavior, route, data-count, and test baseline.
- [complete] Replace compatibility-view reads with direct new-schema queries.
- [complete] Collapse writes to one IMDb-primary implementation.
- [complete] Remove OMDb, legacy rating caches, old-schema helpers, and dead code.
- [complete] Repair or remove stale maintenance scripts and legacy tests.
- [complete] Align templates and labels with the actual IMDb/TMDB source matrix.
- [complete] Run full tests, static checks, database checks, and real HTTP checks.

## Boundaries

- Do not alter migrated `movie.db` or rebuilt `imdb.db` contents.
- Preserve unrelated user changes, especially `scratch/devserver.log`.
- Keep historical migrations 001-010 and rollback databases as history/recovery.
- Do not retain old-schema runtime support solely for historical tests.
- Preserve TMDB/AList import behavior and the 62 provisional episodes.

## Errors encountered

- Requested `_media_macros.html` and `genres.html` during template inspection,
  but the actual filenames differ. No files were changed; subsequent searches
  will enumerate `templates/` before opening exact paths.
- One combined patch attempted to delete and add `models.py` at the same path;
  `apply_patch` rejected multiple operations on one target and made no change.
  Resolution: delete and add in two separately verified patch calls.
- Two read-only checks referenced the old root `schema.sql` path and used a
  PowerShell-incompatible wildcard with `rg`. Neither changed files; the real
  schema path and directory-based search form were used afterward.
- The first native test run found two residual detail contracts: the app still
  expected `vote_count`, and flattened TV sources lacked episode coordinates.
  Both were corrected; the complete replacement suite then passed.
- One verification command used Bash heredoc syntax in PowerShell and failed
  before execution. It was rerun with a PowerShell here-string. Ruff was not
  installed in the project environment, so compilation, an AST unused-import
  audit, tests, diff checks, and live route checks were used instead.
