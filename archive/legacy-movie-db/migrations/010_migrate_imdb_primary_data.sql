/*
 * Copy the legacy TMDB-keyed `movie.db` into the IMDb-primary schema.
 *
 * Preconditions:
 * - the new schema is the `main` database;
 * - the previous database is attached as `legacy`;
 * - `main` is empty.
 */

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

INSERT INTO media (
    media_type,
    imdb_id,
    tmdb_id,
    display_title,
    match_status
)
SELECT 'movie',
       imdb_id,
       movie_id,
       title,
       'matched'
  FROM legacy.movie
 ORDER BY movie_id;

INSERT INTO media (
    media_type,
    imdb_id,
    tmdb_id,
    display_title,
    match_status
)
SELECT 'series',
       imdb_id,
       series_id,
       series_name,
       'matched'
  FROM legacy.tv_series
 ORDER BY series_id;

INSERT INTO media (
    media_type,
    imdb_id,
    tmdb_id,
    parent_media_id,
    season_number,
    episode_number,
    display_title,
    match_status
)
SELECT 'episode',
       episode.imdb_id,
       episode.episode_id,
       parent.media_id,
       season.season_number,
       episode.episode_number,
       episode.episode_name,
       CASE
           WHEN episode.imdb_id IS NULL OR episode.imdb_id = ''
           THEN 'provisional'
           ELSE 'matched'
       END
  FROM legacy.tv_episode AS episode
  JOIN legacy.tv_season AS season
    ON season.season_id = episode.season_id
  JOIN media AS parent
    ON parent.media_type = 'series'
   AND parent.tmdb_id = season.series_id
 ORDER BY season.series_id,
          season.season_number,
          episode.episode_number;

INSERT INTO tmdb_metadata (
    media_id,
    overview,
    tagline,
    homepage,
    status
)
SELECT target.media_id,
       source.overview,
       source.tagline,
       source.homepage,
       source.release_status
  FROM legacy.movie AS source
  JOIN media AS target
    ON target.media_type = 'movie'
   AND target.tmdb_id = source.movie_id;

INSERT INTO tmdb_metadata (
    media_id,
    original_language,
    overview,
    tagline,
    homepage,
    status
)
SELECT target.media_id,
       source.original_language,
       source.overview,
       source.tagline,
       source.homepage,
       source.series_status
  FROM legacy.tv_series AS source
  JOIN media AS target
    ON target.media_type = 'series'
   AND target.tmdb_id = source.series_id;

INSERT INTO tmdb_metadata (
    media_id,
    overview,
    air_date
)
SELECT target.media_id,
       source.overview,
       source.air_date
  FROM legacy.tv_episode AS source
  JOIN media AS target
    ON target.media_type = 'episode'
   AND target.tmdb_id = source.episode_id;

INSERT INTO media_image (
    media_id,
    image_type,
    image_position,
    remote_path,
    local_path
)
SELECT target.media_id,
       CASE source.image_type
           WHEN 'posters'   THEN 'poster'
           WHEN 'logos'     THEN 'logo'
           WHEN 'backdrops' THEN 'backdrop'
       END,
       source.image_position,
       source.image_url,
       source.local_path
  FROM legacy.movie_image AS source
  JOIN media AS target
    ON target.media_type = 'movie'
   AND target.tmdb_id = source.movie_id;

INSERT INTO media_image (
    media_id,
    image_type,
    image_position,
    remote_path,
    local_path
)
SELECT target.media_id,
       CASE source.image_type
           WHEN 'posters'   THEN 'poster'
           WHEN 'logos'     THEN 'logo'
           WHEN 'backdrops' THEN 'backdrop'
       END,
       source.image_position,
       source.image_url,
       source.local_path
  FROM legacy.tv_series_image AS source
  JOIN media AS target
    ON target.media_type = 'series'
   AND target.tmdb_id = source.series_id;

INSERT INTO media_image (
    media_id,
    image_type,
    image_position,
    remote_path,
    local_path
)
SELECT target.media_id,
       'still',
       1,
       episode.still_path,
       source.local_path
  FROM legacy.tv_episode_image AS source
  JOIN legacy.tv_episode AS episode
    ON episode.episode_id = source.episode_id
  JOIN media AS target
    ON target.media_type = 'episode'
   AND target.tmdb_id = source.episode_id;

INSERT INTO media_source (
    media_id,
    alist_path,
    media_duration_ms,
    file_size_bytes,
    bit_rate,
    container_format,
    video_codec,
    video_width,
    video_height,
    video_frame_rate,
    video_pixel_format,
    video_dynamic_range,
    audio_codec,
    audio_channels,
    audio_channel_layout,
    probed_at
)
SELECT target.media_id,
       source.alist_path,
       source.media_duration_ms,
       source.file_size_bytes,
       source.bit_rate,
       source.container_format,
       source.video_codec,
       source.video_width,
       source.video_height,
       source.video_frame_rate,
       source.video_pixel_format,
       source.video_dynamic_range,
       source.audio_codec,
       source.audio_channels,
       source.audio_channel_layout,
       source.probed_at
  FROM legacy.movie_source AS source
  JOIN media AS target
    ON target.media_type = 'movie'
   AND target.tmdb_id = source.movie_id;

INSERT INTO media_source (
    media_id,
    alist_path,
    media_duration_ms,
    file_size_bytes,
    bit_rate,
    container_format,
    video_codec,
    video_width,
    video_height,
    video_frame_rate,
    video_pixel_format,
    video_dynamic_range,
    audio_codec,
    audio_channels,
    audio_channel_layout,
    probed_at
)
SELECT target.media_id,
       source.alist_path,
       source.media_duration_ms,
       source.file_size_bytes,
       source.bit_rate,
       source.container_format,
       source.video_codec,
       source.video_width,
       source.video_height,
       source.video_frame_rate,
       source.video_pixel_format,
       source.video_dynamic_range,
       source.audio_codec,
       source.audio_channels,
       source.audio_channel_layout,
       source.probed_at
  FROM legacy.tv_episode_source AS source
  JOIN media AS target
    ON target.media_type = 'episode'
   AND target.tmdb_id = source.episode_id;

INSERT INTO watch_progress (
    media_id,
    position_ms,
    duration_ms,
    updated_at
)
SELECT target.media_id,
       source.position_ms,
       source.duration_ms,
       source.updated_at
  FROM legacy.movie_watch_progress AS source
  JOIN media AS target
    ON target.media_type = 'movie'
   AND target.tmdb_id = source.movie_id;

INSERT INTO watch_progress (
    media_id,
    position_ms,
    duration_ms,
    updated_at
)
SELECT target.media_id,
       source.position_ms,
       source.duration_ms,
       source.updated_at
  FROM legacy.tv_episode_watch_progress AS source
  JOIN media AS target
    ON target.media_type = 'episode'
   AND target.tmdb_id = source.episode_id;

INSERT INTO favorite_folder (
    folder_id,
    folder_name,
    folder_type,
    icon_name,
    folder_position,
    is_system,
    created_at
)
SELECT folder_id,
       folder_name,
       CASE folder_type
           WHEN 'tv' THEN 'series'
           ELSE folder_type
       END,
       icon_name,
       folder_position,
       is_system,
       created_at
  FROM legacy.favorite_folder
 ORDER BY folder_position
ON CONFLICT (folder_id)
DO UPDATE SET folder_name = excluded.folder_name,
              folder_type = excluded.folder_type,
              icon_name = excluded.icon_name,
              folder_position = excluded.folder_position,
              is_system = excluded.is_system,
              created_at = excluded.created_at;

INSERT INTO favorite_item (
    folder_id,
    media_id,
    added_at
)
SELECT source.folder_id,
       target.media_id,
       source.added_at
  FROM legacy.favorite_item AS source
  JOIN media AS target
    ON source.media_type = 'movie'
   AND target.media_type = 'movie'
   AND target.tmdb_id = source.movie_id;

INSERT INTO favorite_item (
    folder_id,
    media_id,
    added_at
)
SELECT source.folder_id,
       target.media_id,
       source.added_at
  FROM legacy.favorite_item AS source
  JOIN media AS target
    ON source.media_type = 'tv'
   AND target.media_type = 'series'
   AND target.tmdb_id = source.series_id;

COMMIT;

ANALYZE main;
