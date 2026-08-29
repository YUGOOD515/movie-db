# Findings

- The worktree already contains extensive user changes, including a completed
  local `imdb.db` import and uncommitted rating-related migrations.
- The IMDb source database is about 5.2 GB and contains `title_basics`, filtered
  `title_akas`, `title_episode`, `title_ratings`, `title_crew`, and `name_basics`.
- Existing project policy requires reviewable SQL and explicit approval before
  database execution.
- Current schema is TMDB-keyed and contains `collection` and `tv_season`, both
  named by the user as unnecessary in the target model.
- Current `movie.db` contains 28 business tables. Besides core media, source,
  image, progress, and favorite data, it normalizes TMDB genres, countries,
  production companies, spoken languages, collections, and seasons.
- `models.py` joins or writes `tv_season` in import, detail, progress, lookup,
  and statistics paths. Removing it therefore requires coordinated model/query
  changes rather than an isolated `DROP TABLE`.
- Uncommitted migrations 007 and 008 add cached IMDb/external rating columns to
  TMDB-keyed tables. The target design should avoid duplicating local IMDb
  ratings into `movie.db` unless a deliberate snapshot/cache policy is chosen.
- All 9 movies and all 22 series have IMDb IDs found in local `title_basics` and
  `title_ratings`.
- Of 490 episodes, 428 have IMDb IDs; all 428 match local IMDb parent-series,
  season-number, and episode-number data exactly. The 62 unmatched episodes are
  four complete future seasons (`人鱼`, `入戏`, `小芳`, `铁拳教育`).
- The target therefore needs an explicit provisional TMDB-only state or a
  quarantine strategy; requiring IMDb IDs immediately would hide or discard
  currently stored future episodes.
- Current `models._connect()` opens only `movie.db`. IMDb-primary reads will
  require attaching the read-only `imdb.db` per connection or a dedicated
  catalog repository layer.
- Current Chinese alias coverage is complete for all 9 movies and 22 series,
  but only 12 of 22 series titles exactly equal one stored Chinese alias. A
  local `display_title` should therefore be retained as presentation data,
  with IMDb aliases as the automatic fallback.
- Current source paths and image local paths have no cross-table collisions, so
  movie/episode source tables and movie/series/episode image tables can be
  merged under global unique-path constraints without data loss.
- Expected preserved facts at migration time: 521 media rows, 728 source rows,
  854 image rows, 13 progress rows, 3 favorite folders, and 0 favorite items.
