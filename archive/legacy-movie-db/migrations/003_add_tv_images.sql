PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE tv_series_image (
    series_id       INTEGER NOT NULL,
    image_type      TEXT    NOT NULL,
    image_position  INTEGER NOT NULL,
    image_url       TEXT    NOT NULL,
    local_path      TEXT    NOT NULL,
    PRIMARY KEY (series_id, image_type, image_position),
         UNIQUE (local_path),
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT tv_series_image_type
          CHECK (image_type IN ('posters', 'logos', 'backdrops')),
     CONSTRAINT tv_series_image_position_range
          CHECK (image_position BETWEEN 1 AND 5)
);

CREATE TABLE tv_episode_image (
    episode_id INTEGER NOT NULL,
    local_path TEXT    NOT NULL,
    PRIMARY KEY (episode_id),
         UNIQUE (local_path),
    FOREIGN KEY (episode_id)
     REFERENCES tv_episode (episode_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE
);

COMMIT;
