# Progress

- Read the updated `AGENTS.MD` and current `helpers.py`.
- Confirmed that only a reviewable SQL file is authorized; execution is not.
- Initialized a scoped planning record for the database schema task.
- Reviewed the linked SQL style guide and TMDB movie-details reference.
- Inspected live movie-detail payloads for standalone and collection movies.
- Confirmed all nested object fields and the current top-level `softcore` field.
- Completed a normalized schema design accounting for every current detail
  field and excluding only duplicate movie poster/backdrop paths.
- Created `schema.sql` with the complete draft DDL and no data mutations.
- Added source-order columns for every nested list so normalization does not
  discard array order.
- Static review found and corrected two overlong constraint names.
- Final static review passed: 21 complete statements, 12 tables, balanced
  parentheses, no mapped-field omissions, no overlong identifiers, no trailing
  whitespace, and no database file created.
