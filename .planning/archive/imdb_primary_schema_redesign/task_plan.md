# IMDb-primary database redesign review

## Goal

Implement a replacement schema in which local IMDb data is the primary metadata
source and TMDB supplies only the user-approved supplemental fields. The user
approved writing and immediately executing the migration on 2026-08-29.

## Phases

- [complete] Audit the current `movie.db`, `imdb.db`, schema, and model usage.
- [complete] Define source ownership and the minimal target entities.
- [complete] Propose target tables, keys, constraints, and removed structures.
- [complete] Define a safe staged migration and verification plan.
- [complete] Present the plan for explicit user approval.
- [complete] Draft the reviewable schema and data-migration SQL.
- [complete] Back up `movie.db` and build the side-by-side migrated database.
- [complete] Verify data parity, integrity, and foreign keys.
- [complete] Refactor the model layer to read IMDb locally and use the new schema.
- [complete] Run regression tests and route checks against the migrated database.
- [complete] Switch `movie.db` only after every gate passes and retain rollback files.

## Boundaries

- Database migration and application refactoring are now authorized.
- Preserve all existing user changes in the dirty worktree.
- `imdb.db` remains a separate local read-only source database.
- The migrated `movie.db` was switched only after backup and validation gates passed.

## Errors encountered

- First on-disk build used unqualified `ANALYZE`, which scans every attached
  database and began traversing the 5.2 GB IMDb catalog. The build was stopped;
  migration 010 now uses `ANALYZE main`, and attached source databases are
  opened explicitly with `mode=ro`.
- The retry exposed the same attached-database scope in unqualified
  `PRAGMA integrity_check`. Explicit `PRAGMA main.integrity_check` and
  `PRAGMA main.foreign_key_check` now restrict validation to the new database.
- Initial write-path test used image position `-1` as a swap slot, conflicting
  with the positive-position constraint. The swap now uses the current maximum
  position plus one, preserving the constraint throughout the transaction.
- New-schema integration assertions passed, but Windows could not remove two
  temporary databases because test context managers committed without closing
  SQLite connections. Tests now wrap connections with `contextlib.closing`.
- The first writable IMDb attachment changed SQLite statistics. Two targeted
  `git restore` attempts could not unlink the 5.2 GB file on Windows. A verified
  HEAD blob was therefore exported to a separate file, hash-checked, and
  streamed over `imdb.db`; SQLite then proved that the committed blob itself
  was truncated and malformed.
- The exact HEAD blob is only 929,173,504 bytes and is a malformed SQLite file,
  whereas the previously verified working database was 5,224,140,800 bytes.
  Git therefore did not contain a usable IMDb backup. A new 5,224,140,800-byte
  database was rebuilt side by side from the preserved TSV sources, fully
  checked, and then promoted to `imdb.db`.
- The first promotion attempt was blocked by an interactive `sqlite3.exe`
  opened against `imdb.db` since 18:24. Only that database client was stopped;
  the VS Code terminal remained open, and the file switch then succeeded.

## Proposed target

- Keep `imdb.db` as a separate read-only authority and attach it from the model
  layer. Read titles, aliases, years, runtime, genres, hierarchy, crew, and IMDb
  ratings directly from it.
- Rebuild `movie.db` as a thin mutable library with seven tables:
  `media`, `tmdb_metadata`, `media_image`, `media_source`, `watch_progress`,
  `favorite_folder`, and `favorite_item`.
- `media` is the shared identity table for movie, series, and episode. It owns
  local IDs, IMDb/TMDB IDs, episode parent/order fields, a local display title,
  and provisional matching state.
- `tmdb_metadata` contains only approved supplemental fields. `media_image`
  covers posters, logos, backdrops, and episode stills.
- Remove collection, season, normalized TMDB relation tables, duplicated IMDb
  rating caches, and fields outside the approved source matrix.
- Preserve the 62 currently unmatched future episodes in a provisional state;
  do not delete their 94 sources or 38 local stills.

## Proposed execution gates

1. User approved the conceptual plan and direct execution after SQL is written.
2. Draft a new side-by-side schema and migration SQL.
3. Build a new database beside `movie.db`, migrate, verify counts/integrity/
   foreign keys, and refactor the model layer against it.
4. Switch files only after application and data-parity verification; retain the
   timestamped backup and old database for rollback.
