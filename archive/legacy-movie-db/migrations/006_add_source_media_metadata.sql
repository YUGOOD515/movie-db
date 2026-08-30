PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

ALTER TABLE movie_source
  ADD COLUMN media_duration_ms INTEGER
      CONSTRAINT movie_source_duration_nonnegative
           CHECK (media_duration_ms IS NULL OR media_duration_ms >= 0);

ALTER TABLE movie_source
  ADD COLUMN file_size_bytes INTEGER
      CONSTRAINT movie_source_size_nonnegative
           CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0);

ALTER TABLE movie_source
  ADD COLUMN bit_rate INTEGER
      CONSTRAINT movie_source_bitrate_nonnegative
           CHECK (bit_rate IS NULL OR bit_rate >= 0);

ALTER TABLE movie_source ADD COLUMN container_format TEXT;
ALTER TABLE movie_source ADD COLUMN video_codec TEXT;

ALTER TABLE movie_source
  ADD COLUMN video_width INTEGER
      CONSTRAINT movie_source_width_positive
           CHECK (video_width IS NULL OR video_width > 0);

ALTER TABLE movie_source
  ADD COLUMN video_height INTEGER
      CONSTRAINT movie_source_height_positive
           CHECK (video_height IS NULL OR video_height > 0);

ALTER TABLE movie_source
  ADD COLUMN video_frame_rate NUMERIC
      CONSTRAINT movie_source_frame_rate_positive
           CHECK (video_frame_rate IS NULL OR video_frame_rate > 0);

ALTER TABLE movie_source ADD COLUMN video_pixel_format TEXT;
ALTER TABLE movie_source ADD COLUMN video_dynamic_range TEXT;
ALTER TABLE movie_source ADD COLUMN audio_codec TEXT;

ALTER TABLE movie_source
  ADD COLUMN audio_channels INTEGER
      CONSTRAINT movie_source_audio_channels_positive
           CHECK (audio_channels IS NULL OR audio_channels > 0);

ALTER TABLE movie_source ADD COLUMN audio_channel_layout TEXT;
ALTER TABLE movie_source ADD COLUMN probed_at TEXT;

ALTER TABLE tv_episode_source
  ADD COLUMN media_duration_ms INTEGER
      CONSTRAINT tv_source_duration_nonnegative
           CHECK (media_duration_ms IS NULL OR media_duration_ms >= 0);

ALTER TABLE tv_episode_source
  ADD COLUMN file_size_bytes INTEGER
      CONSTRAINT tv_source_size_nonnegative
           CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0);

ALTER TABLE tv_episode_source
  ADD COLUMN bit_rate INTEGER
      CONSTRAINT tv_source_bitrate_nonnegative
           CHECK (bit_rate IS NULL OR bit_rate >= 0);

ALTER TABLE tv_episode_source ADD COLUMN container_format TEXT;
ALTER TABLE tv_episode_source ADD COLUMN video_codec TEXT;

ALTER TABLE tv_episode_source
  ADD COLUMN video_width INTEGER
      CONSTRAINT tv_source_width_positive
           CHECK (video_width IS NULL OR video_width > 0);

ALTER TABLE tv_episode_source
  ADD COLUMN video_height INTEGER
      CONSTRAINT tv_source_height_positive
           CHECK (video_height IS NULL OR video_height > 0);

ALTER TABLE tv_episode_source
  ADD COLUMN video_frame_rate NUMERIC
      CONSTRAINT tv_source_frame_rate_positive
           CHECK (video_frame_rate IS NULL OR video_frame_rate > 0);

ALTER TABLE tv_episode_source ADD COLUMN video_pixel_format TEXT;
ALTER TABLE tv_episode_source ADD COLUMN video_dynamic_range TEXT;
ALTER TABLE tv_episode_source ADD COLUMN audio_codec TEXT;

ALTER TABLE tv_episode_source
  ADD COLUMN audio_channels INTEGER
      CONSTRAINT tv_source_audio_channels_positive
           CHECK (audio_channels IS NULL OR audio_channels > 0);

ALTER TABLE tv_episode_source ADD COLUMN audio_channel_layout TEXT;
ALTER TABLE tv_episode_source ADD COLUMN probed_at TEXT;

COMMIT;
