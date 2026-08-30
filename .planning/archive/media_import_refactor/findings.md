# Findings

- The root currently contains five Python modules: `app.py`, `db.py`,
  `helpers.py`, `models.py`, and `playback.py`.
- `scripts/fetch_tmdb_metadata.py` is complete but is not called by the app.
- `helpers.py` still contains an obsolete second TMDB and image-download path.
- The seven-table `library.db` schema already represents titles, episode
  relations, artwork, media files, playback, and collections; no migration is
  required for the import workflow.
- `imdb.db` has indexed Title ID, primary-title, alias-title, and parent episode
  lookups and remains the authority for identity, type, hierarchy, and ratings.
- The `/media` association form is currently disabled and has no POST route.
- The pre-change suite passes 18/18 tests.
- Existing user edits in `viewmodels/title.py` and `tests/test_library_app.py`
  change default series selection to the resume episode; they must be preserved.
- The live app calls only `helpers.get_video_link()` and `helpers.play_video()`;
  the old TMDB/image helper APIs survive only in connector tests and the obsolete
  `scripts/populate_library.py` migration.
- Current root-module size is 1,305 lines, including 742 lines in `helpers.py`.
- A Series Title ID must resolve through season and episode coordinates because
  root-series media files are not consumed by the series detail playback UI.
- The import writer must preserve existing non-null TMDB fields when a later
  bundle lacks metadata for an IMDb episode; otherwise a repeat series import
  can erase previously stored supplements.
- Artwork downloads are staged outside the final image tree and only rows with
  verified local files are written. Moved files are compensated if the database
  transaction or a later filesystem move fails.
