PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

DROP TABLE movie_image;

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

CREATE INDEX source_movie_lookup_idx
          ON movie_source (movie_id);

COMMIT;
