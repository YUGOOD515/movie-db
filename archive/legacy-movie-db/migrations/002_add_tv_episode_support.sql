PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

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
