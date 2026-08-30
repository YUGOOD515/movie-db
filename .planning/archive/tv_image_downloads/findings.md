# Findings

- Current TV persistence stores only remote TMDB file paths in `tv_series`,
  `tv_season`, and `tv_episode`; no TV local-image tables exist.
- Existing retry-aware `_download_image` can be reused for TV assets.
- The selected episode is already isolated before model persistence, so one
  episode still can be downloaded without downloading every season still.
- Migration 003 and the fresh baseline each create two image tables with clean
  integrity and foreign-key checks.
- Series art is stored under `images/tv/{series_id}/{category}`; selected stills
  use `images/tv/{series_id}/season-{number}/episodes/{episode_id}.{suffix}`.
- A persisted `tv_series_image` row set is the cache boundary: later episode
  imports reuse it, while each selected episode still is independently saved.
- Live TMDB validation returned 13 Chinese poster candidates, 5 Chinese logos,
  and 134 backdrops for Breaking Bad. The first import stored 15 series images;
  the second episode import preserved those 15 and produced two episode stills
  and two source mappings with clean integrity and foreign keys.
- Production backup `movie-before-tv-images-20260827-222051-sqlite.db` has
  SHA-256 `15D2DBA31FD87358411D69B4082284F44112AEB90750F8AAADB029C765AFE582`.
- Migration 003 created both production image tables with integrity `ok` and no
  foreign-key violations; production currently has no TV rows to backfill.
- The running Flask server auto-reloaded and returns HTTP 200 after migration.
