PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

ALTER TABLE tv_episode ADD COLUMN imdb_id TEXT;

ALTER TABLE tv_episode
  ADD COLUMN imdb_rating REAL
      CONSTRAINT tv_episode_imdb_rating_range
           CHECK (imdb_rating IS NULL OR (imdb_rating >= 0 AND imdb_rating <= 10));

ALTER TABLE tv_episode ADD COLUMN imdb_rating_updated_at TEXT;

COMMIT;
