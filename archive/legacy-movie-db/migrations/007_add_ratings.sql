PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

ALTER TABLE movie
  ADD COLUMN imdb_rating NUMERIC
      CONSTRAINT movie_imdb_rating_range
           CHECK (imdb_rating IS NULL OR (imdb_rating >= 0 AND imdb_rating <= 10));

ALTER TABLE movie
  ADD COLUMN imdb_vote_count INTEGER
      CONSTRAINT movie_imdb_vote_count_nonnegative
           CHECK (imdb_vote_count IS NULL OR imdb_vote_count >= 0);

ALTER TABLE movie
  ADD COLUMN rotten_tomatoes_tomatometer INTEGER
      CONSTRAINT movie_rt_tomatometer_range
           CHECK (rotten_tomatoes_tomatometer IS NULL OR (rotten_tomatoes_tomatometer >= 0 AND rotten_tomatoes_tomatometer <= 100));

ALTER TABLE movie
  ADD COLUMN rotten_tomatoes_popcornmeter INTEGER
      CONSTRAINT movie_rt_popcornmeter_range
           CHECK (rotten_tomatoes_popcornmeter IS NULL OR (rotten_tomatoes_popcornmeter >= 0 AND rotten_tomatoes_popcornmeter <= 100));

ALTER TABLE movie
  ADD COLUMN metacritic_rating INTEGER
      CONSTRAINT movie_metacritic_rating_range
           CHECK (metacritic_rating IS NULL OR (metacritic_rating >= 0 AND metacritic_rating <= 100));

ALTER TABLE movie ADD COLUMN ratings_updated_at TEXT;

ALTER TABLE tv_series ADD COLUMN imdb_id TEXT;

ALTER TABLE tv_series
  ADD COLUMN imdb_rating NUMERIC
      CONSTRAINT tv_series_imdb_rating_range
           CHECK (imdb_rating IS NULL OR (imdb_rating >= 0 AND imdb_rating <= 10));

ALTER TABLE tv_series
  ADD COLUMN imdb_vote_count INTEGER
      CONSTRAINT tv_series_imdb_vote_count_nonnegative
           CHECK (imdb_vote_count IS NULL OR imdb_vote_count >= 0);

ALTER TABLE tv_series
  ADD COLUMN rotten_tomatoes_tomatometer INTEGER
      CONSTRAINT tv_series_rt_tomatometer_range
           CHECK (rotten_tomatoes_tomatometer IS NULL OR (rotten_tomatoes_tomatometer >= 0 AND rotten_tomatoes_tomatometer <= 100));

ALTER TABLE tv_series
  ADD COLUMN rotten_tomatoes_popcornmeter INTEGER
      CONSTRAINT tv_series_rt_popcornmeter_range
           CHECK (rotten_tomatoes_popcornmeter IS NULL OR (rotten_tomatoes_popcornmeter >= 0 AND rotten_tomatoes_popcornmeter <= 100));

ALTER TABLE tv_series
  ADD COLUMN metacritic_rating INTEGER
      CONSTRAINT tv_series_metacritic_rating_range
           CHECK (metacritic_rating IS NULL OR (metacritic_rating >= 0 AND metacritic_rating <= 100));

ALTER TABLE tv_series ADD COLUMN ratings_updated_at TEXT;

COMMIT;
