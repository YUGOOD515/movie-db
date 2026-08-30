/*
 * IMDb-primary MovieDB schema.
 *
 * `imdb.db` remains the read-only authority for titles, aliases, years,
 * runtimes, genres, hierarchy, crew, and IMDb ratings. This mutable database
 * stores only local library state and the approved TMDB supplements.
 */

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE media (
    media_id         INTEGER NOT NULL,
    media_type       TEXT    NOT NULL,
    imdb_id          TEXT,
    tmdb_id          INTEGER,
    parent_media_id  INTEGER,
    season_number    INTEGER,
    episode_number   INTEGER,
    display_title    TEXT    NOT NULL,
    match_status     TEXT    NOT NULL DEFAULT 'matched',
    created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (media_id),
         UNIQUE (imdb_id),
         UNIQUE (media_type, tmdb_id),
         UNIQUE (parent_media_id, season_number, episode_number),
    FOREIGN KEY (parent_media_id)
     REFERENCES media (media_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT media_type_valid
          CHECK (media_type IN ('movie', 'series', 'episode')),
     CONSTRAINT media_identity_available
          CHECK (imdb_id IS NOT NULL OR tmdb_id IS NOT NULL),
     CONSTRAINT media_imdb_id_format
          CHECK (imdb_id IS NULL OR imdb_id GLOB 'tt[0-9]*'),
     CONSTRAINT media_tmdb_id_positive
          CHECK (tmdb_id IS NULL OR tmdb_id > 0),
     CONSTRAINT media_match_status_valid
          CHECK (
              (
                  match_status = 'matched'
                  AND imdb_id IS NOT NULL
              )
              OR
              (
                  match_status = 'provisional'
                  AND media_type = 'episode'
                  AND imdb_id IS NULL
                  AND tmdb_id IS NOT NULL
              )
          ),
     CONSTRAINT media_hierarchy_valid
          CHECK (
              (
                  media_type IN ('movie', 'series')
                  AND parent_media_id IS NULL
                  AND season_number IS NULL
                  AND episode_number IS NULL
              )
              OR
              (
                  media_type = 'episode'
                  AND parent_media_id IS NOT NULL
                  AND season_number IS NOT NULL
                  AND season_number >= 0
                  AND episode_number IS NOT NULL
                  AND episode_number >= 1
              )
          )
);

CREATE TABLE tmdb_metadata (
    media_id          INTEGER NOT NULL,
    original_language TEXT,
    overview          TEXT,
    tagline           TEXT,
    homepage          TEXT,
    status            TEXT,
    air_date          TEXT,
    synced_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (media_id),
    FOREIGN KEY (media_id)
     REFERENCES media (media_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT tmdb_air_date_format
          CHECK (
              air_date IS NULL
              OR air_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
          )
);

CREATE TABLE media_image (
    media_id       INTEGER NOT NULL,
    image_type     TEXT    NOT NULL,
    image_position INTEGER NOT NULL,
    remote_path    TEXT,
    local_path     TEXT    NOT NULL,
    PRIMARY KEY (media_id, image_type, image_position),
         UNIQUE (local_path),
    FOREIGN KEY (media_id)
     REFERENCES media (media_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT media_image_type_valid
          CHECK (image_type IN ('poster', 'logo', 'backdrop', 'still')),
     CONSTRAINT media_image_position_positive
          CHECK (image_position >= 1)
);

CREATE TABLE media_source (
    source_id            INTEGER NOT NULL,
    media_id             INTEGER NOT NULL,
    alist_path           TEXT    NOT NULL,
    media_duration_ms    INTEGER,
    file_size_bytes      INTEGER,
    bit_rate             INTEGER,
    container_format     TEXT,
    video_codec          TEXT,
    video_width          INTEGER,
    video_height         INTEGER,
    video_frame_rate     NUMERIC,
    video_pixel_format   TEXT,
    video_dynamic_range  TEXT,
    audio_codec          TEXT,
    audio_channels       INTEGER,
    audio_channel_layout TEXT,
    probed_at            TEXT,
    PRIMARY KEY (source_id),
         UNIQUE (alist_path),
    FOREIGN KEY (media_id)
     REFERENCES media (media_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT media_source_duration_nonnegative
          CHECK (media_duration_ms IS NULL OR media_duration_ms >= 0),
     CONSTRAINT media_source_size_nonnegative
          CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
     CONSTRAINT media_source_bitrate_nonnegative
          CHECK (bit_rate IS NULL OR bit_rate >= 0),
     CONSTRAINT media_source_width_positive
          CHECK (video_width IS NULL OR video_width > 0),
     CONSTRAINT media_source_height_positive
          CHECK (video_height IS NULL OR video_height > 0),
     CONSTRAINT media_source_frame_rate_positive
          CHECK (video_frame_rate IS NULL OR video_frame_rate > 0),
     CONSTRAINT media_source_audio_channels_positive
          CHECK (audio_channels IS NULL OR audio_channels > 0)
);

CREATE TABLE watch_progress (
    media_id    INTEGER NOT NULL,
    position_ms INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (media_id),
    FOREIGN KEY (media_id)
     REFERENCES media (media_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT watch_position_nonnegative
          CHECK (position_ms >= 0),
     CONSTRAINT watch_duration_nonnegative
          CHECK (duration_ms >= 0),
     CONSTRAINT watch_position_within_duration
          CHECK (duration_ms = 0 OR position_ms <= duration_ms)
);

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
          CHECK (folder_type IN ('movie', 'series', 'custom')),
     CONSTRAINT favorite_folder_system_boolean
          CHECK (is_system IN (0, 1)),
     CONSTRAINT favorite_folder_position_positive
          CHECK (folder_position >= 1)
);

CREATE TABLE favorite_item (
    favorite_item_id INTEGER NOT NULL,
    folder_id        INTEGER NOT NULL,
    media_id         INTEGER NOT NULL,
    added_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (favorite_item_id),
         UNIQUE (folder_id, media_id),
    FOREIGN KEY (folder_id)
     REFERENCES favorite_folder (folder_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (media_id)
     REFERENCES media (media_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE
);

CREATE INDEX media_type_lookup_idx
          ON media (media_type);

CREATE INDEX episode_parent_lookup_idx
          ON media (parent_media_id, season_number, episode_number)
       WHERE media_type = 'episode';

CREATE INDEX media_source_lookup_idx
          ON media_source (media_id);

CREATE INDEX media_image_lookup_idx
          ON media_image (media_id, image_type, image_position);

CREATE INDEX watch_progress_updated_idx
          ON watch_progress (updated_at DESC);

CREATE INDEX favorite_item_folder_added_idx
          ON favorite_item (folder_id, added_at DESC);

INSERT INTO favorite_folder (
    folder_id,
    folder_name,
    folder_type,
    icon_name,
    folder_position,
    is_system
)
     VALUES (1, '电影收藏', 'movie', 'film', 1, 1),
            (2, '电视剧收藏', 'series', 'tv', 2, 1),
            (3, '自定义收藏', 'custom', 'bookmark-star', 3, 1);

COMMIT;
