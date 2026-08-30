# Findings

- `search_id()` returns TMDB movie details in Simplified Chinese.
- `search_img()` returns `posters`, `logos`, and `backdrops` as complete URLs.
- `poster_path` and `backdrop_path` from movie details duplicate the image data
  domain and should not be stored in the movie table.
- Image storage must enforce categories and positions 1 through 5.
- SQL must follow the project's linked SQL style guide.
- The style guide requires descriptive lower-case snake_case names, upper-case
  SQL keywords, meaningful grouping/indentation, keys on every table, named
  non-key constraints, and range checks where values have known bounds.
- The TMDB movie details endpoint is the authoritative source for the movie
  object; the live payload still needs inspection because the reference page
  does not enumerate every response field in its rendered text.
- A live `search_id(157336)` payload has 27 top-level fields: `adult`,
  `backdrop_path`, `belongs_to_collection`, `budget`, `genres`, `homepage`,
  `id`, `imdb_id`, `origin_country`, `original_language`, `original_title`,
  `overview`, `popularity`, `poster_path`, `production_companies`,
  `production_countries`, `release_date`, `revenue`, `runtime`, `softcore`,
  `spoken_languages`, `status`, `tagline`, `title`, `video`, `vote_average`,
  and `vote_count`.
- `belongs_to_collection` contains `id`, `name`, `poster_path`, and
  `backdrop_path`. These collection-level paths are not duplicates of the
  movie images returned by `search_img()` and must remain stored.
- Production companies contain `id`, `logo_path`, `name`, and
  `origin_country`; production countries contain `iso_3166_1` and `name`;
  spoken languages contain `english_name`, `iso_639_1`, and `name`.
- The normalized design will use one movie table plus collection, genre,
  country, company, language, relationship, and movie-image tables. Only the
  movie-level `poster_path` and `backdrop_path` fields are omitted as duplicate
  image data.
- Position columns on relationship tables preserve the order of TMDB arrays;
  the movie-image composite key and range check limit each category to source
  positions 1 through 5.
