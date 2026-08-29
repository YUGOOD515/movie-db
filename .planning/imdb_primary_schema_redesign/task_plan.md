# IMDb-primary database redesign review

## Goal

Design a reviewable replacement schema in which local IMDb data is the primary
metadata source and TMDB supplies only the user-approved supplemental fields.
Do not draft or execute DDL, migrate data, or change application code until the
user approves this plan.

## Phases

- [complete] Audit the current `movie.db`, `imdb.db`, schema, and model usage.
- [complete] Define source ownership and the minimal target entities.
- [complete] Propose target tables, keys, constraints, and removed structures.
- [complete] Define a safe staged migration and verification plan.
- [complete] Present the plan for explicit user approval.
- [pending] After approval only: draft reviewable SQL; execute only after a second explicit approval if requested.

## Boundaries

- Read-only review in this phase.
- Preserve all existing user changes in the dirty worktree.
- `imdb.db` remains a separate local source database unless review shows a clear reason to copy data.
- `movie.db` remains untouched until an approved migration with backup and validation gates.

## Errors encountered

- None.

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

1. User approves or revises this conceptual plan.
2. Draft a new side-by-side schema and migration SQL for review; do not execute.
3. After explicit SQL approval, build a new database beside `movie.db`, migrate,
   verify counts/integrity/foreign keys, and refactor the model layer against it.
4. Switch files only after application and data-parity verification; retain the
   timestamped backup and old database for rollback.
