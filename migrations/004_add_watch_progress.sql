PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE movie_watch_progress (
    movie_id    INTEGER NOT NULL,
    position_ms INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (movie_id),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT movie_watch_position_nonnegative
          CHECK (position_ms >= 0),
     CONSTRAINT movie_watch_duration_nonnegative
          CHECK (duration_ms >= 0),
     CONSTRAINT movie_watch_position_within_duration
          CHECK (duration_ms = 0 OR position_ms <= duration_ms)
);

CREATE TABLE tv_episode_watch_progress (
    episode_id  INTEGER NOT NULL,
    position_ms INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (episode_id),
    FOREIGN KEY (episode_id)
     REFERENCES tv_episode (episode_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT tv_episode_watch_position_nonnegative
          CHECK (position_ms >= 0),
     CONSTRAINT tv_episode_watch_duration_nonnegative
          CHECK (duration_ms >= 0),
     CONSTRAINT tv_episode_watch_position_within_duration
          CHECK (duration_ms = 0 OR position_ms <= duration_ms)
);

CREATE INDEX movie_watch_progress_updated_idx
          ON movie_watch_progress (updated_at);

CREATE INDEX tv_episode_watch_progress_updated_idx
          ON tv_episode_watch_progress (updated_at);

COMMIT;
