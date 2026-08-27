# Movie import workflow plan

## Goal

Migrate `movie.db` to the approved schema, then implement a compact Bootstrap
form and Flask workflow that maps a local AList path to a TMDB movie, stores all
normalized metadata, downloads the selected images locally, and lists every
existing mapping.

## Phases

- [complete] Inspect the current schema, database, Flask app, helpers, and templates.
- [complete] Back up and migrate the SQLite database without losing data.
- [complete] Implement URL validation, TMDB/image import, normalized persistence, and mapping queries.
- [complete] Build the Bootstrap form, feedback messages, and mapping table.
- [complete] Verify database constraints, Flask POST/GET behavior, and rendered output.

## Boundaries

- Accept only URLs matching the configured local AList origin.
- Store AList virtual paths, not arbitrary external URLs.
- Store downloaded images under Flask `static/images/` and persist relative paths.
- Preserve user changes outside files required by this task.

## Errors encountered

- The planning skill's documented session-catchup script is not installed at
  the expected `.opencode` path; current files are used as source of truth.
- A compound filesystem replacement command was blocked by the destructive
  action safety policy before execution. The migration approach was changed to
  an SQLite-native backup plus an explicit incremental migration.
- The first `pragma_table_info` verification query treated `notnull` as an
  unquoted keyword. The migration itself had succeeded; the read-only check was
  corrected by quoting the column name.
- The first whole-file `app.py` patch attempted delete and add operations for
  the same path in one patch, which the patch tool rejected without changes.
  The replacement was split into two sequential patch operations.
- Initial Windows test cleanup failed because the test fixture used the SQLite
  connection context manager without explicitly closing the connection. The
  fixture now wraps connections with `contextlib.closing`.
