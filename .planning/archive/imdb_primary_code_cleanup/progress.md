# Progress

- Activated the planning-with-files workflow for the comprehensive cleanup.
- Reviewed prior migration findings and the current dirty worktree.
- Identified compatibility views, dual write paths, obsolete OMDb code, stale
  scripts, dead helpers, and duplicated rating semantics.
- Enumerated production model/helper call sites and reviewed the seven-table
  schema plus current homepage/library template contracts.
- Logged two harmless template filename lookup errors and changed the inspection
  approach to enumerate exact filenames first.
- Established a 30/30 test baseline and captured keys/counts for homepage,
  library, filters, favorites, overview, movie detail, and series detail.
- Completed the baseline phase and began direct-query model design.
- Reviewed library filters, card/detail template contracts, the native-schema
  tests, legacy tests, connector flow, and helper boundaries.
- Decided to replace the legacy test suite with focused native-schema and helper
  tests after the direct model is in place.
- Logged a no-op whole-file patch format error; switched to separate delete/add
  operations as required by the patch tool.
- Replaced `models.py` with a direct seven-table/IMDb implementation, reducing
  it from 4,133 to 1,148 lines.
- Verified compilation plus homepage, overview, library, filters, movie detail,
  and series detail contracts against the live migrated databases.
- Completed direct reads and single-path writes; began connector/helper cleanup.
- Removed the unreachable OMDb/rating-cache synchronization path, the misspelled
  TMDB alias, the unused mapping wrapper, and the dead JSON debug helper.
- Changed TMDB detail retrieval to always merge the IMDb external ID required
  by the new movie and series write path; compilation passes with no remaining
  runtime references to the removed compatibility APIs.
- Updated rating and metadata labels to the actual source matrix, removed the
  country filter and obsolete RT/Metacritic/TMDB rating assets and CSS.
- Fixed three maintenance scripts to resolve imports, `movie.db`, manifests,
  and images from the project root.
- Replaced the 2,447-line legacy-schema test suite and its 744-line fixture
  with 12 native integration/unit tests covering routes, writes, IMDb identity,
  TMDB external IDs, AList validation, ffprobe normalization, and image limits.
- Fixed the two contracts exposed by the first native run; 12/12 tests pass.
- Renamed the remaining `vote_average` compatibility projection to the explicit
  `imdb_rating` contract and removed the final unused mapping/auth helpers.
- Final verification passes: 13/13 tests, Python compilation, unused-import
  audit, `git diff --check`, SQLite integrity/foreign-key checks, six live HTTP
  routes, and 544 rendered local static references with zero missing files.
- Confirmed live counts remain unchanged at 521 media, 728 sources, 854 images,
  and 62 provisional episodes; no migrated database contents were altered.
