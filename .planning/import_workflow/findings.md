# Findings

- The migrated `movie.db` has 13 tables and 7 indexes; integrity and foreign-key
  checks pass and all content tables remain empty before workflow testing.
- `schema.sql` now defines 13 tables, including `movie_source`, and adds the
  required `movie_image.local_path` column plus a seventh index.
- `app.py` has a placeholder POST branch, compares `request.method` with lower-case
  `post`, does not fetch result rows, and has no import/storage workflow.
- `index.html` contains only headings and a refresh button.
- `layout.html` already loads Bootstrap 5.3.8 and exposes a centered main block.
- `search_movie()`, `search_id()`, and `search_img()` provide the necessary TMDB
  calls; `search_img()` returns Chinese posters/logos and all backdrops.
- The input workflow selects the first TMDB search result, downloads up to five
  images per category, stores paths relative to Flask `static/`, and upserts an
  AList path so an existing path can be deliberately remapped.
- Final verification leaves the formal database empty but migrated: 13 tables,
  7 indexes, integrity `ok`, and no foreign-key violations.
