# Findings

- Audit started from clean commit `79f25f2`.
- Prior history shows repeated template, schema, and compatibility cleanups; all
  current assumptions must be revalidated against the committed tree.
- The tracked active surface has 108 historical `.planning` files spread across
  31 completed or superseded task directories; they should be nested under a
  planning archive so tooling no longer treats them as active plans.
- `.workbuddy/memory/2026-08-30.md` is tracked even though `.workbuddy/` is now
  explicitly ignored; it is tool-specific historical state, not project documentation.
- Ignored generated directories include `.flask_session`, `.playwright-cli`,
  `.pytest_cache`, every `__pycache__`, `output/playwright`, and `scratch`.
- The root contains a tracked `library.db.bak-20260830-234111`; durable database
  backups otherwise live under ignored `backups/`, so the root copy is misplaced.
- `migrations/001` through `010` and `migrations/schema.sql` describe the retired
  `movie.db` lineage. Only `migrations/library_schema.sql` defines the active
  seven-table library schema.
- `scripts/migrate_artwork_paths.py` is a completed one-time migration, while
  `scripts/import_imdb.py` and `scripts/fetch_tmdb_metadata.py` remain active utilities.
- Existing user edits are present in `static/site.js`, `static/style.css`, and
  `templates/detail.html`; the cleanup must preserve them.
- All nine current templates are reached by Flask/Jinja references, and all five
  tracked files under `static/` are referenced; none is a deletion candidate.
- `python-dotenv` was declared but neither imported nor used by the application;
  it has been removed from the project and lock file.
- `migrations/009_create_imdb_primary_schema.sql` and `migrations/schema.sql`
  are byte-identical, in addition to both belonging to the retired schema lineage.
- `AGENTS.MD` still links to the completed artwork-path migration and overstates
  the TMDB fields retained by the current seven-table schema.
- The IMDb import utility expects six TSV datasets. The two retained files under
  `imdb/` are therefore an incomplete source cache, not a complete rebuild set.
- A byte-level check confirms the Chinese text in `README.md`, `AGENTS.MD`, and
  `scripts/import_imdb.py` is valid UTF-8. The apparent mojibake came from the
  PowerShell output encoding, not from file corruption.
- The artwork audit found exactly 854 database paths and 854 disk files, with
  zero missing files and zero orphans. The image tree is fully protected.
- Every current package module is imported by the runtime or another current
  module. The apparent single-reference functions are registered Flask routes
  or unittest hooks, not dead code.
- Seventeen historical database backups pass SQLite `quick_check`. The 929 MB
  `imdb-git-malformed-20260829-204843.db` fails with `database disk image is
  malformed`; its filename and validation both establish it as disposable.
- No database backups are byte-identical, so the remaining valid snapshots
  should be organized by lineage rather than deduplicated.
- Historical plans now live under `.planning/archive/`; only this audit remains
  in the active planning area.
- The active `migrations/` directory now contains only `library_schema.sql`, and
  `scripts/` contains only the two reusable IMDb/TMDB utilities.
- The duplicate legacy `migrations/schema.sql` was deleted rather than archived;
  its identical numbered copy remains in the legacy archive and Git retains history.
- Valid database snapshots are grouped under `backups/library/` and
  `backups/legacy-movie-db/`. The misplaced root backup was preserved but is no
  longer tracked by Git.
- The malformed IMDb backup and all selected generated directories were removed,
  reclaiming roughly 958 MB. This deletion is not recoverable from the working
  tree; the tracked changes remain recoverable through Git.
- Final validation passed: 19 unit/integration tests, active-source compilation,
  Flask route loading, in-memory seven-table schema creation, `git diff --check`,
  and read-only quick/foreign-key checks for both live databases.
