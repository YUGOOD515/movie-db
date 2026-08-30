# Progress

- Read the planning skill and current project/memory constraints.
- Inspected worktree status and the completed IMDb dataset-import plan.
- Began a read-only audit of current DDL and field-source mapping.
- Mapped current table declarations and direct model/test dependencies for
  `collection`, `tv_season`, and normalized TMDB relation tables.
- Verified live row counts, integrity, foreign keys, IMDb match coverage, and
  episode hierarchy consistency without modifying either database.
- Completed the conceptual source matrix, seven-table target, provisional
  episode policy, and side-by-side migration/rollback gates for user review.
- Submitted the plan for user review. No database execution is authorized yet.
- User approved direct execution after the SQL is written. Confirmed Git HEAD
  `4723c70` and found only the pre-existing `scratch/devserver.log` modification.
- Added migrations 009 and 010, then executed both against an in-memory target.
  All seven tables, data counts, constraints, paths, integrity, and foreign keys
  validated without touching the on-disk databases.
- Added `scripts/migrate_imdb_primary.py` to create a SQLite backup, build the
  side-by-side target, and enforce row/path/IMDb/integrity validation gates.
- Stopped the first on-disk build after tracing its delay to unqualified
  `ANALYZE`. Restricted analysis to `main` and made both attached inputs
  explicitly read-only before retrying.
- Stopped the second build after proving unqualified `integrity_check` also
  traversed attached IMDb; schema-qualified both validation pragmas.
- Successfully built `movie.imdb-primary-20260829-202925.db` and backup
  `backups/movie-pre-imdb-primary-20260829-202925.db`. All expected/actual
  counts matched; integrity, foreign keys, path parity, IMDb IDs, and episode
  hierarchy passed.
- Added the IMDb-backed compatibility read layer and direct seven-table writes.
  Legacy tests passed 27/27; new-schema homepage/detail/overview/genre/favorite
  routes and movie/episode/source/progress/image writes passed on a disposable
  database with integrity `ok` and zero foreign-key errors.
- Stopped project Flask processes, rebuilt from the quiesced legacy database,
  and switched the validated target to `movie.db`. Retained both the SQLite
  backup and original legacy file under `backups/`.
- Final verification passed 30 tests and nine HTTP route checks. The live
  database has seven tables, 521 media rows, 728 sources, 854 images, and 15
  progress rows; integrity is `ok` and foreign-key errors are zero.
- Restored `imdb.db` byte-for-byte from Git HEAD after detecting the failed
  initial analysis had changed it, then discovered that the committed 929 MB
  blob itself is malformed. Began source-based recovery from the original TSVs.
- Rebuilt `imdb.db` from the six preserved TSV datasets. Exact table counts,
  the five expected indexes, a known-title sample, full `integrity_check`, and
  foreign-key checks all passed.
- Verified all 459 populated media IMDb IDs exist in the rebuilt catalog and
  all 428 matched episodes retain the exact IMDb parent/season/episode tuple;
  62 future episodes remain explicitly provisional.
- Preserved the malformed committed blob as
  `backups/imdb-git-malformed-20260829-204843.db` and promoted the validated
  5.224 GB rebuild to `imdb.db`.
- Final live regression passed 30/30 tests and all nine library/detail HTTP
  routes. `movie.db` and `imdb.db` both return integrity `ok`; foreign-key
  violations are zero.
- Restarted the Flask development service after database promotion. A real
  request to `http://127.0.0.1:5000/` returned HTTP 200 without using or
  overwriting the existing `scratch/devserver.log`.
- Rewrote every local Git reference to remove `imdb.db`, including the
  non-commit Codex baseline tree; expired reflogs and pruned the two 929 MB
  blobs. The Git object pack is now about 4.37 MB, while the valid working
  database remains intact and ignored.
