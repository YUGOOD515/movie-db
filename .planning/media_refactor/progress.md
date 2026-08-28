# Progress

- Read the planning skill and initialized a scoped refactor plan.
- Measured function lengths, mapped references, inspected current diffs and tests,
  and identified the safe merge/split boundaries.
- Replaced movie/TV-specific TMDB search, details, image lookup, and image
  download wrappers with media-type-aware helpers.
- Extracted image target-path and atomic file replacement mechanics.
- Unified movie/TV genre, country, company, language, image, and AList source
  persistence behind internal table configuration.
- Split TV season catalog, episode pointer, and still-image persistence from the
  main series transaction orchestration.
- Moved form parsing and dispatch out of the `/media` route.
- Updated tests for the shared APIs; the project-venv suite currently passes.
- Final verification: 9 tests pass, both GET routes return 200, production
  SQLite integrity is `ok`, and `foreign_key_check` returns no rows.
