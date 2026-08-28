PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE favorite_folder (
    folder_id       INTEGER NOT NULL,
    folder_name     TEXT    NOT NULL,
    folder_type     TEXT    NOT NULL,
    icon_name       TEXT    NOT NULL,
    folder_position INTEGER NOT NULL,
    is_system       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (folder_id),
         UNIQUE (folder_name),
         UNIQUE (folder_position),
     CONSTRAINT favorite_folder_type_valid
          CHECK (folder_type IN ('movie', 'tv', 'custom')),
     CONSTRAINT favorite_folder_system_boolean
          CHECK (is_system IN (0, 1)),
     CONSTRAINT favorite_folder_position_positive
          CHECK (folder_position >= 1)
);

CREATE TABLE favorite_item (
    favorite_item_id INTEGER NOT NULL,
    folder_id        INTEGER NOT NULL,
    media_type       TEXT    NOT NULL,
    movie_id         INTEGER,
    series_id        INTEGER,
    added_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (favorite_item_id),
         UNIQUE (folder_id, movie_id),
         UNIQUE (folder_id, series_id),
    FOREIGN KEY (folder_id)
     REFERENCES favorite_folder (folder_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT favorite_item_media_valid
          CHECK (
              (
                  media_type = 'movie'
                  AND movie_id IS NOT NULL
                  AND series_id IS NULL
              )
              OR
              (
                  media_type = 'tv'
                  AND movie_id IS NULL
                  AND series_id IS NOT NULL
              )
          )
);

INSERT INTO favorite_folder (
    folder_id,
    folder_name,
    folder_type,
    icon_name,
    folder_position,
    is_system
)
     VALUES (1, '电影收藏', 'movie', 'film', 1, 1),
            (2, '电视剧收藏', 'tv', 'tv', 2, 1),
            (3, '自定义收藏', 'custom', 'bookmark-star', 3, 1);

CREATE INDEX favorite_item_folder_added_idx
          ON favorite_item (folder_id, added_at DESC);

CREATE INDEX favorite_item_movie_idx
          ON favorite_item (movie_id);

CREATE INDEX favorite_item_series_idx
          ON favorite_item (series_id);

COMMIT;
