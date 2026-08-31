/*
 * Local media library schema.
 *
 * This schema creates a new, empty library database. It does not import or
 * migrate existing data.
 */

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE title_basic (
    title_id          TEXT    NOT NULL,
    tmdb_id           INTEGER,
    title_type        TEXT    NOT NULL,
    primary_title     TEXT,
    original_title    TEXT,
    original_language TEXT,
    display_title     TEXT    NOT NULL,
    is_adult          INTEGER NOT NULL DEFAULT 0,
    start_year        INTEGER,
    end_year          INTEGER,
    runtime_minutes   INTEGER,
    genres            TEXT,
    overview              TEXT,
    tagline               TEXT,
    average_rating        REAL,
    num_votes             INTEGER,
    mdblist_score         INTEGER,
    mdblist_score_average INTEGER,
    rt_critics_score      INTEGER,
    rt_critics_votes      INTEGER,
    rt_audience_score     INTEGER,
    rt_audience_votes     INTEGER,
    metacritic_score      INTEGER,
    metacritic_votes      INTEGER,
    metacritic_user_score REAL,
    metacritic_user_votes INTEGER,
    trakt_score           INTEGER,
    trakt_votes           INTEGER,
    letterboxd_score      REAL,
    letterboxd_votes      INTEGER,
    roger_ebert_rating    REAL,
    myanimelist_score     REAL,
    myanimelist_votes     INTEGER,
    added_at              TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (title_id),
    CONSTRAINT title_basic_type_valid
         CHECK (title_type IN ('movie', 'series', 'episode')),
    CONSTRAINT title_basic_id_format
         CHECK (title_id GLOB 'tt[0-9]*'),
    CONSTRAINT title_basic_adult_valid
         CHECK (is_adult IN (0, 1)),
    CONSTRAINT title_basic_years_valid
         CHECK (
             start_year IS NULL
             OR end_year IS NULL
             OR end_year >= start_year
         ),
    CONSTRAINT title_basic_runtime_nonnegative
         CHECK (runtime_minutes IS NULL OR runtime_minutes >= 0)
);

CREATE TABLE title_relation (
    episode_id     TEXT    NOT NULL,
    series_id      TEXT    NOT NULL,
    season_number  INTEGER,
    episode_number INTEGER,
    PRIMARY KEY (episode_id),
         UNIQUE (series_id, season_number, episode_number),
    FOREIGN KEY (episode_id)
     REFERENCES title_basic (title_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (series_id)
     REFERENCES title_basic (title_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    CONSTRAINT title_relation_season_nonnegative
         CHECK (season_number IS NULL OR season_number >= 0),
    CONSTRAINT title_relation_episode_positive
         CHECK (episode_number IS NULL OR episode_number >= 1)
);

CREATE TABLE title_artwork (
    title_id     TEXT    NOT NULL,
    artwork_type TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    remote_path  TEXT,
    local_path   TEXT    NOT NULL,
    PRIMARY KEY (title_id, artwork_type, position),
    FOREIGN KEY (title_id)
     REFERENCES title_basic (title_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    CONSTRAINT title_artwork_type_valid
         CHECK (
             artwork_type IN (
                 'poster',
                 'landscape_poster',
                 'logo',
                 'still'
             )
         ),
    CONSTRAINT title_artwork_position_positive
         CHECK (position >= 1)
);

CREATE TABLE media_file (
    file_id              INTEGER NOT NULL,
    title_id             TEXT    NOT NULL,
    alist_path           TEXT    NOT NULL,
    duration_ms          INTEGER,
    file_size_bytes      INTEGER,
    bit_rate             INTEGER,
    container_format     TEXT,
    video_codec          TEXT,
    video_width          INTEGER,
    video_height         INTEGER,
    video_frame_rate     REAL,
    video_pixel_format   TEXT,
    video_dynamic_range  TEXT,
    audio_codec          TEXT,
    audio_channels       INTEGER,
    audio_channel_layout TEXT,
    probed_at            TEXT,
    PRIMARY KEY (file_id),
         UNIQUE (alist_path),
    FOREIGN KEY (title_id)
     REFERENCES title_basic (title_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    CONSTRAINT media_file_duration_nonnegative
         CHECK (duration_ms IS NULL OR duration_ms >= 0),
    CONSTRAINT media_file_size_nonnegative
         CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    CONSTRAINT media_file_bitrate_nonnegative
         CHECK (bit_rate IS NULL OR bit_rate >= 0),
    CONSTRAINT media_file_width_positive
         CHECK (video_width IS NULL OR video_width > 0),
    CONSTRAINT media_file_height_positive
         CHECK (video_height IS NULL OR video_height > 0),
    CONSTRAINT media_file_frame_rate_positive
         CHECK (video_frame_rate IS NULL OR video_frame_rate > 0),
    CONSTRAINT media_file_audio_channels_positive
         CHECK (audio_channels IS NULL OR audio_channels > 0)
);

CREATE TABLE playback_progress (
    playback_id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER NOT NULL,
    position_ms INTEGER NOT NULL DEFAULT 0,
    watched_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (file_id)
     REFERENCES media_file (file_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    CONSTRAINT playback_progress_position_nonnegative
         CHECK (position_ms >= 0)
);

CREATE TABLE collection (
    collection_id   INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    collection_type TEXT    NOT NULL,
    title_type      TEXT,
    icon_name       TEXT,
    "order"         INTEGER NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_id),
         UNIQUE (name),
    CONSTRAINT collection_type_valid
         CHECK (collection_type IN ('system', 'custom')),
    CONSTRAINT collection_title_type_valid
         CHECK (title_type IS NULL OR title_type IN ('movie', 'series'))
);

CREATE TABLE collection_item (
    collection_id INTEGER NOT NULL,
    title_id      TEXT    NOT NULL,
    added_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (collection_id, title_id),
    FOREIGN KEY (collection_id)
     REFERENCES collection (collection_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (title_id)
     REFERENCES title_basic (title_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE
);

CREATE INDEX title_relation_series_id_index
          ON title_relation (series_id);

CREATE INDEX media_file_title_id_index
          ON media_file (title_id);

CREATE INDEX playback_progress_file_watched_index
          ON playback_progress (file_id, watched_at DESC);

CREATE INDEX collection_item_title_id_index
          ON collection_item (title_id);

COMMIT;
