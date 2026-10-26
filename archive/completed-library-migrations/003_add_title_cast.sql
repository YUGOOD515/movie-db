PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

CREATE TABLE title_cast (
    title_id        TEXT    NOT NULL,
    tmdb_person_id  INTEGER NOT NULL,
    name            TEXT    NOT NULL,
    character_name  TEXT,
    cast_order      INTEGER NOT NULL,
    remote_path     TEXT,
    local_path      TEXT,
    PRIMARY KEY (title_id, tmdb_person_id),
    FOREIGN KEY (title_id)
     REFERENCES title_basic (title_id)
      ON DELETE CASCADE
      ON UPDATE CASCADE,
    CONSTRAINT title_cast_person_positive
         CHECK (tmdb_person_id > 0),
    CONSTRAINT title_cast_order_nonnegative
         CHECK (cast_order >= 0)
);

COMMIT;
