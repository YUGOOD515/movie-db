/*
 * Draft SQLite schema. Review and approve before execution.
 *
 * Data mapping:
 * - movie details come from search_id();
 * - movie-level poster_path and backdrop_path are omitted because search_img()
 *   supplies their complete URLs;
 * - collection image paths and production-company logo paths remain because
 *   they describe different entities and are not returned by search_img();
 * - relationship positions preserve the order of arrays returned by TMDB;
 * - movie_image stores search_img()[category][:5] in one-based source order;
 * - local_path values are paths relative to Flask's static directory;
 * - movie_source maps AList virtual file paths to their corresponding movie.
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

COMMIT;
