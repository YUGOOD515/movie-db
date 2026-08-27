# Database schema approval plan

## Goal

Draft an SQLite schema that retains all non-image movie details returned by
`search_id()` and stores at most the first five URLs from each `search_img()`
category. Deliver SQL for user review without executing it.

## Phases

- [complete] Confirm API payload fields and SQL style constraints.
- [complete] Design normalized movie, lookup, relation, and image tables.
- [complete] Write the schema to one SQL file.
- [complete] Perform static review only; do not create a database.

## Approval boundary

Do not execute the SQL or create/modify any SQLite database until the user
explicitly approves the SQL file.

## Errors encountered

- The skill's documented session-catchup script was not installed at the
  expected `.opencode` path. No earlier planning session existed in this repo.
- Initial static review found two constraint identifiers longer than the style
  guide's 30-character recommendation. They were shortened without changing
  their checks.
