# Findings

- Current MDBList requests, CLI multi-ID fetching, benchmark fetching, bulk dump loop, database updates, and media batch import are sequential.
- `dump_library_ratings.py` currently cannot compile because `from migrations.002_add_ratings_to_title_basic ...` is invalid Python syntax.
- `library.db` already contains rating columns; 31 of 36 movie/series roots currently have MDBList-derived values.
- Existing global bottom-center toast work is uncommitted and must be preserved and extended rather than replaced.
- `services.media_import.import_media()` has a natural concurrency point after local IMDb resolution and source-conflict checks: MDBList can run in one worker while AList validation/probing and TMDB metadata continue on the request thread.
- `import_media_bundle()` is already the atomic write boundary. Passing optional root rating fields into it avoids a second post-import transaction.
- The existing overview header can hold one small refresh button without changing the card ViewModel. A POST start endpoint plus GET status endpoint is sufficient for polling.
- A process-local daemon thread and locked status dictionary are adequate for this personal local app; no persistent queue or new dependency is justified.
