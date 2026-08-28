/*
 * Draft SQLite schema. Review and approve before execution.
 *
 * Data mapping:
 * - movie details come from get_media_details();
 * - movie-level poster_path and backdrop_path are omitted because
 *   search_media_images()
 *   supplies their complete URLs;
 * - collection image paths and production-company logo paths remain because
 *   they describe different entities and are not returned by
 *   search_media_images();
 * - relationship positions preserve the order of arrays returned by TMDB;
 * - movie_image stores search_media_images()[category][:5] in one-based order;
 * - local_path values are paths relative to Flask's static directory;
 * - movie_source maps AList virtual file paths to their corresponding movie.
 * - TV imports retain series data, every episode in the selected season, and
 *   one AList source relationship for the manually selected episode;
 * - nested TMDB creator, network, crew, guest-star and runtime arrays remain
 *   JSON because they have no shared movie entity and must be preserved whole.
 * - tv_series_image stores the first five TV images per category;
 * - tv_episode_image stores the local still for each manually linked episode.
 */

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE collection (
    collection_id   INTEGER NOT NULL,
    collection_name TEXT    NOT NULL,
    poster_path     TEXT,
    backdrop_path   TEXT,
    PRIMARY KEY (collection_id)
);

CREATE TABLE movie (
    movie_id          INTEGER NOT NULL,
    collection_id     INTEGER,
    adult             INTEGER NOT NULL,
    softcore          INTEGER NOT NULL,
    video             INTEGER NOT NULL,
    budget            INTEGER NOT NULL,
    revenue           INTEGER NOT NULL,
    runtime           INTEGER,
    popularity        NUMERIC NOT NULL,
    vote_average      NUMERIC NOT NULL,
    vote_count        INTEGER NOT NULL,
    original_language TEXT    NOT NULL,
    original_title    TEXT    NOT NULL,
    title             TEXT    NOT NULL,
    overview          TEXT,
    tagline           TEXT,
    homepage          TEXT,
    imdb_id           TEXT,
    release_date      TEXT,
    release_status    TEXT    NOT NULL,
    PRIMARY KEY (movie_id),
    FOREIGN KEY (collection_id)
     REFERENCES collection (collection_id)
      ON DELETE SET NULL
      ON UPDATE CASCADE,
     CONSTRAINT movie_adult_boolean
          CHECK (adult IN (0, 1)),
     CONSTRAINT movie_softcore_boolean
          CHECK (softcore IN (0, 1)),
     CONSTRAINT movie_video_boolean
          CHECK (video IN (0, 1)),
     CONSTRAINT movie_budget_nonnegative
          CHECK (budget >= 0),
     CONSTRAINT movie_revenue_nonnegative
          CHECK (revenue >= 0),
     CONSTRAINT movie_runtime_nonnegative
          CHECK (runtime IS NULL OR runtime >= 0),
     CONSTRAINT movie_popularity_nonnegative
          CHECK (popularity >= 0),
     CONSTRAINT movie_vote_average_range
          CHECK (vote_average BETWEEN 0 AND 10),
     CONSTRAINT movie_vote_count_nonnegative
          CHECK (vote_count >= 0)
);

CREATE TABLE movie_source (
    source_id  INTEGER NOT NULL,
    movie_id   INTEGER NOT NULL,
    alist_path TEXT    NOT NULL,
    PRIMARY KEY (source_id),
         UNIQUE (alist_path),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE
);

CREATE TABLE genre (
    genre_id   INTEGER NOT NULL,
    genre_name TEXT    NOT NULL,
    PRIMARY KEY (genre_id)
);

CREATE TABLE movie_genre (
    movie_id       INTEGER NOT NULL,
    genre_id       INTEGER NOT NULL,
    genre_position INTEGER NOT NULL,
    PRIMARY KEY (movie_id, genre_id),
         UNIQUE (movie_id, genre_position),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (genre_id)
     REFERENCES genre (genre_id)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT genre_position_positive
          CHECK (genre_position >= 1)
);

CREATE TABLE country (
    country_code TEXT NOT NULL,
    country_name TEXT,
    PRIMARY KEY (country_code),
     CONSTRAINT country_code_length
          CHECK (country_code = '' OR LENGTH(country_code) = 2)
);

CREATE TABLE movie_origin_country (
    movie_id         INTEGER NOT NULL,
    country_code     TEXT    NOT NULL,
    country_position INTEGER NOT NULL,
    PRIMARY KEY (movie_id, country_code),
         UNIQUE (movie_id, country_position),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (country_code)
     REFERENCES country (country_code)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT origin_country_pos_check
          CHECK (country_position >= 1)
);

CREATE TABLE movie_production_country (
    movie_id         INTEGER NOT NULL,
    country_code     TEXT    NOT NULL,
    country_position INTEGER NOT NULL,
    PRIMARY KEY (movie_id, country_code),
         UNIQUE (movie_id, country_position),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (country_code)
     REFERENCES country (country_code)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT production_country_pos_check
          CHECK (country_position >= 1)
);

CREATE TABLE production_company (
    company_id          INTEGER NOT NULL,
    company_name        TEXT    NOT NULL,
    origin_country_code TEXT,
    logo_path           TEXT,
    PRIMARY KEY (company_id),
    FOREIGN KEY (origin_country_code)
     REFERENCES country (country_code)
      ON DELETE SET NULL
      ON UPDATE CASCADE
);

CREATE TABLE movie_production_company (
    movie_id         INTEGER NOT NULL,
    company_id       INTEGER NOT NULL,
    company_position INTEGER NOT NULL,
    PRIMARY KEY (movie_id, company_id),
         UNIQUE (movie_id, company_position),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (company_id)
     REFERENCES production_company (company_id)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT company_position_positive
          CHECK (company_position >= 1)
);

CREATE TABLE spoken_language (
    language_code TEXT NOT NULL,
    english_name  TEXT NOT NULL,
    native_name   TEXT NOT NULL,
    PRIMARY KEY (language_code),
     CONSTRAINT language_code_length
          CHECK (LENGTH(language_code) = 2)
);

CREATE TABLE movie_spoken_language (
    movie_id          INTEGER NOT NULL,
    language_code     TEXT    NOT NULL,
    language_position INTEGER NOT NULL,
    PRIMARY KEY (movie_id, language_code),
         UNIQUE (movie_id, language_position),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (language_code)
     REFERENCES spoken_language (language_code)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT language_position_positive
          CHECK (language_position >= 1)
);

CREATE TABLE movie_image (
    movie_id       INTEGER NOT NULL,
    image_type     TEXT    NOT NULL,
    image_position INTEGER NOT NULL,
    image_url      TEXT    NOT NULL,
    local_path     TEXT    NOT NULL,
    PRIMARY KEY (movie_id, image_type, image_position),
         UNIQUE (local_path),
    FOREIGN KEY (movie_id)
     REFERENCES movie (movie_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT movie_image_type
          CHECK (image_type IN ('posters', 'logos', 'backdrops')),
     CONSTRAINT movie_image_position_range
          CHECK (image_position BETWEEN 1 AND 5)
);

CREATE TABLE tv_series (
    series_id            INTEGER NOT NULL,
    adult                INTEGER NOT NULL,
    softcore             INTEGER NOT NULL,
    in_production        INTEGER NOT NULL,
    number_of_episodes   INTEGER NOT NULL,
    number_of_seasons    INTEGER NOT NULL,
    popularity           NUMERIC NOT NULL,
    vote_average         NUMERIC NOT NULL,
    vote_count           INTEGER NOT NULL,
    original_language    TEXT    NOT NULL,
    original_name        TEXT    NOT NULL,
    series_name          TEXT    NOT NULL,
    overview             TEXT,
    tagline              TEXT,
    homepage             TEXT,
    first_air_date       TEXT,
    last_air_date        TEXT,
    series_status        TEXT    NOT NULL,
    series_type          TEXT    NOT NULL,
    poster_path          TEXT,
    backdrop_path        TEXT,
    created_by_json      TEXT    NOT NULL,
    networks_json        TEXT    NOT NULL,
    episode_runtime_json TEXT    NOT NULL,
    last_episode_id      INTEGER,
    next_episode_id      INTEGER,
    PRIMARY KEY (series_id),
    FOREIGN KEY (last_episode_id)
     REFERENCES tv_episode (episode_id)
      ON DELETE SET NULL
      ON UPDATE CASCADE,
    FOREIGN KEY (next_episode_id)
     REFERENCES tv_episode (episode_id)
      ON DELETE SET NULL
      ON UPDATE CASCADE,
     CONSTRAINT tv_series_adult_boolean
          CHECK (adult IN (0, 1)),
     CONSTRAINT tv_series_softcore_boolean
          CHECK (softcore IN (0, 1)),
     CONSTRAINT tv_series_in_production_boolean
          CHECK (in_production IN (0, 1)),
     CONSTRAINT tv_series_episode_count_nonnegative
          CHECK (number_of_episodes >= 0),
     CONSTRAINT tv_series_season_count_nonnegative
          CHECK (number_of_seasons >= 0),
     CONSTRAINT tv_series_popularity_nonnegative
          CHECK (popularity >= 0),
     CONSTRAINT tv_series_vote_average_range
          CHECK (vote_average BETWEEN 0 AND 10),
     CONSTRAINT tv_series_vote_count_nonnegative
          CHECK (vote_count >= 0)
);

CREATE TABLE tv_season (
    season_id        INTEGER NOT NULL,
    series_id        INTEGER NOT NULL,
    season_number    INTEGER NOT NULL,
    episode_count    INTEGER NOT NULL,
    season_name      TEXT    NOT NULL,
    overview         TEXT,
    air_date         TEXT,
    poster_path      TEXT,
    vote_average     NUMERIC NOT NULL,
    tmdb_internal_id TEXT,
    networks_json    TEXT    NOT NULL,
    PRIMARY KEY (season_id),
         UNIQUE (series_id, season_number),
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT tv_season_number_nonnegative
          CHECK (season_number >= 0),
     CONSTRAINT tv_season_episode_count_nonnegative
          CHECK (episode_count >= 0),
     CONSTRAINT tv_season_vote_average_range
          CHECK (vote_average BETWEEN 0 AND 10)
);

CREATE TABLE tv_episode (
    episode_id       INTEGER NOT NULL,
    season_id        INTEGER NOT NULL,
    episode_number   INTEGER NOT NULL,
    episode_name     TEXT    NOT NULL,
    overview         TEXT,
    air_date         TEXT,
    runtime          INTEGER,
    production_code  TEXT,
    episode_type     TEXT,
    still_path       TEXT,
    vote_average     NUMERIC NOT NULL,
    vote_count       INTEGER NOT NULL,
    crew_json        TEXT    NOT NULL,
    guest_stars_json TEXT    NOT NULL,
    PRIMARY KEY (episode_id),
         UNIQUE (season_id, episode_number),
    FOREIGN KEY (season_id)
     REFERENCES tv_season (season_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
     CONSTRAINT tv_episode_number_positive
          CHECK (episode_number >= 1),
     CONSTRAINT tv_episode_runtime_nonnegative
          CHECK (runtime IS NULL OR runtime >= 0),
     CONSTRAINT tv_episode_vote_average_range
          CHECK (vote_average BETWEEN 0 AND 10),
     CONSTRAINT tv_episode_vote_count_nonnegative
          CHECK (vote_count >= 0)
);

CREATE TABLE tv_episode_source (
    source_id  INTEGER NOT NULL,
    episode_id INTEGER NOT NULL,
    alist_path TEXT    NOT NULL,
    PRIMARY KEY (source_id),
         UNIQUE (alist_path),
    FOREIGN KEY (episode_id)
     REFERENCES tv_episode (episode_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE
);

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

CREATE TABLE tv_series_genre (
    series_id      INTEGER NOT NULL,
    genre_id       INTEGER NOT NULL,
    genre_position INTEGER NOT NULL,
    PRIMARY KEY (series_id, genre_id),
         UNIQUE (series_id, genre_position),
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (genre_id)
     REFERENCES genre (genre_id)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT tv_genre_position_positive
          CHECK (genre_position >= 1)
);

CREATE TABLE tv_series_origin_country (
    series_id        INTEGER NOT NULL,
    country_code     TEXT    NOT NULL,
    country_position INTEGER NOT NULL,
    PRIMARY KEY (series_id, country_code),
         UNIQUE (series_id, country_position),
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (country_code)
     REFERENCES country (country_code)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT tv_origin_country_position_positive
          CHECK (country_position >= 1)
);

CREATE TABLE tv_series_production_country (
    series_id        INTEGER NOT NULL,
    country_code     TEXT    NOT NULL,
    country_position INTEGER NOT NULL,
    PRIMARY KEY (series_id, country_code),
         UNIQUE (series_id, country_position),
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (country_code)
     REFERENCES country (country_code)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT tv_production_country_position_positive
          CHECK (country_position >= 1)
);

CREATE TABLE tv_series_production_company (
    series_id        INTEGER NOT NULL,
    company_id       INTEGER NOT NULL,
    company_position INTEGER NOT NULL,
    PRIMARY KEY (series_id, company_id),
         UNIQUE (series_id, company_position),
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (company_id)
     REFERENCES production_company (company_id)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT tv_company_position_positive
          CHECK (company_position >= 1)
);

CREATE TABLE tv_series_spoken_language (
    series_id         INTEGER NOT NULL,
    language_code     TEXT    NOT NULL,
    language_position INTEGER NOT NULL,
    PRIMARY KEY (series_id, language_code),
         UNIQUE (series_id, language_position),
    FOREIGN KEY (series_id)
     REFERENCES tv_series (series_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    FOREIGN KEY (language_code)
     REFERENCES spoken_language (language_code)
      ON DELETE RESTRICT
      ON UPDATE CASCADE,
     CONSTRAINT tv_language_position_positive
          CHECK (language_position >= 1)
);

CREATE INDEX movie_collection_idx
          ON movie (collection_id);

CREATE INDEX source_movie_lookup_idx
          ON movie_source (movie_id);

CREATE INDEX genre_lookup_idx
          ON movie_genre (genre_id);

CREATE INDEX origin_country_lookup_idx
          ON movie_origin_country (country_code);

CREATE INDEX production_country_lookup_idx
          ON movie_production_country (country_code);

CREATE INDEX production_company_lookup_idx
          ON movie_production_company (company_id);

CREATE INDEX spoken_language_lookup_idx
          ON movie_spoken_language (language_code);

CREATE INDEX tv_series_last_episode_idx
          ON tv_series (last_episode_id);

CREATE INDEX tv_series_next_episode_idx
          ON tv_series (next_episode_id);

CREATE INDEX tv_season_series_lookup_idx
          ON tv_season (series_id);

CREATE INDEX tv_episode_season_lookup_idx
          ON tv_episode (season_id);

CREATE INDEX tv_episode_source_lookup_idx
          ON tv_episode_source (episode_id);

CREATE INDEX movie_watch_progress_updated_idx
          ON movie_watch_progress (updated_at);

CREATE INDEX tv_episode_watch_progress_updated_idx
          ON tv_episode_watch_progress (updated_at);

CREATE INDEX tv_series_genre_lookup_idx
          ON tv_series_genre (genre_id);

CREATE INDEX tv_origin_country_lookup_idx
          ON tv_series_origin_country (country_code);

CREATE INDEX tv_production_country_lookup_idx
          ON tv_series_production_country (country_code);

CREATE INDEX tv_production_company_lookup_idx
          ON tv_series_production_company (company_id);

CREATE INDEX tv_spoken_language_lookup_idx
          ON tv_series_spoken_language (language_code);

COMMIT;
