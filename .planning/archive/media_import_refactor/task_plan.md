# Media import and root-module refactor

## Goal

Enable the media manager to import a movie, series, or episode from a user-
supplied IMDb Title ID and AList URL, while consolidating root Python modules
into clear model, connector, and service packages.

## Phases

- [complete] Record the current worktree, routes, schema, tests, and reusable code.
- [complete] Extract database, connector, and playback modules without behavior changes.
- [complete] Implement IMDb/TMDB/AList import orchestration and atomic library writes.
- [complete] Enable the manager import form and success/error feedback.
- [complete] Remove obsolete legacy scripts and duplicate TMDB helper code.
- [complete] Add regression tests and verify the complete application.

## Boundaries

- Accept an explicit IMDb Title ID for movie, series, or episode imports.
- Treat `imdb.db` as read-only and do not mutate the live `library.db` during tests.
- Preserve current uncommitted user changes and the existing seven-table schema.
- Keep the completed TMDB CLI contract while sharing its implementation with the app.
- Do not silently remap an AList path already associated with another title.

## Errors encountered

- The first post-extraction test run had four expected import-migration errors:
  legacy tests still called the root `models` and patched `helpers` while the app
  now imports package modules. One assertion also still expected the disabled
  form. Resolution: migrate the tests with the production imports before deleting
  the compatibility root files.
