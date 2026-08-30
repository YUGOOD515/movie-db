# Findings

- Production uses only the seven-table IMDb-primary `movie.db`.
- `models.py` still creates 26 legacy-shape TEMP views; 12 are always empty.
- Runtime contains 21 `_is_imdb_primary` branches and old movie/TV write paths.
- OMDb synchronization is bypassed in `app.py`, but helpers, models, templates,
  assets, and legacy tests still retain the obsolete rating pipeline.
- The compatibility layer maps IMDb ratings into `vote_average`, causing the
  UI to label the same value as both IMDb and TMDB.
- Three maintenance scripts resolve `movie.db` and image paths relative to the
  `scripts` directory and are stale for the current checkout.
- Historical migration SQL is not considered redundant and will be retained.
- Production callers require 28 model APIs from `app.py` and maintenance
  scripts. Public behavior can be preserved with a much smaller direct-query
  model instead of retaining legacy table-shaped queries.
- The seven-table schema stores local identity/state only; title, year, runtime,
  genres, hierarchy, and ratings must be joined from attached IMDb tables.
- The current top-rated UI copy still says TMDB although ranking now uses IMDb.
- Baseline tests pass 30/30. Homepage contracts contain 31 top-level media
  (9 movies and 22 series); live detail contracts were captured for one movie
  and one three-season series before refactoring.
- Existing filter data has 17 genres, five series original languages, no
  countries, and 12 title years. Countries are empty by design under the new
  source matrix and should be removed from the filter contract.
- Existing tests are dominated by a 2,447-line legacy-schema suite; the native
  IMDb-primary suite has only three integration tests. Cleanup should replace
  the legacy suite with focused helper and native-schema coverage rather than
  preserving old tables for test compatibility.
- The direct model can preserve templates through a single top-level media
  projection plus focused detail/continue-watching queries; this avoids one
  query implementation per deleted legacy table.
- The replacement model is 1,148 lines versus 4,133 lines, with no TEMP views,
  legacy schema detection, dual writes, collection relations, or rating cache.
  Baseline media counts and sampled movie/series detail source/season counts
  match after the replacement.
- TV detail responses do not intrinsically contain an IMDb ID. The connector
  now fills a missing identity from TMDB `/external_ids` so both import paths
  satisfy the IMDb-primary requirement.
- Movie detail responses usually include `imdb_id` directly, so the connector
  reuses it and only calls TMDB `/external_ids` when the detail response lacks
  the identity (normally series). This avoids one needless movie API call.
