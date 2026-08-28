# Findings

- Exact helper duplication exists in movie/TV search, detail retrieval, image
  retrieval, and image-collection wrapper functions.
- `_download_image_collection` (56 lines) mixes path construction, transfer,
  failure policy, and record creation; selected-still download duplicates its
  file-write mechanics.
- Model control-heavy functions are `_save_movie` (290 lines), `_save_tv_episode`
  (238), and `_save_tv_series_relations` (132). Much of their length is SQL and
  value mapping, but movie/TV genre, country, company, and language handling is
  genuinely duplicated.
- App `media` (49 lines) combines form parsing, dispatch, and HTTP response;
  `import_tv_episode` (48) is a coherent orchestration but repeats initial TMDB
  search/detail validation with `import_movie`.
- Current test suite has eight workflow tests and patches type-specific helper
  names; tests must follow the new owning APIs.
- `templates/media.html` is the current user-renamed media template; preserve it.
- The remaining long `_save_movie`, `_save_tv_episode`, `_upsert_tv_season`, and
  `_upsert_tv_episode` bodies are dominated by declarative value mappings and
  SQL, so further line-count-driven splitting would violate the requested
  boundary.
- Production `movie.db` passes SQLite integrity and foreign-key checks after the
  code-only refactor; GET `/` and GET `/media` both return 200.
