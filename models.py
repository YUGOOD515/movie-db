import sqlite3
from contextlib import closing
from pathlib import Path


WATCH_RESUME_MIN_MS = 10_000
WATCH_COMPLETE_REMAINING_MS = 120_000
WATCH_COMPLETE_RATIO = 0.95
SOURCE_MEDIA_COLUMNS = (
    "media_duration_ms", "file_size_bytes", "bit_rate", "container_format",
    "video_codec", "video_width", "video_height", "video_frame_rate",
    "video_pixel_format", "video_dynamic_range", "audio_codec",
    "audio_channels", "audio_channel_layout",
)
IMAGE_TYPE_TO_STORAGE = {
    "posters": "poster", "logos": "logo", "backdrops": "backdrop",
}


def _connect(database_path: str | Path):
    database_text = str(database_path)
    if database_text == ":memory:":
        connection = sqlite3.connect(database_text, timeout=5)
    else:
        resolved = Path(database_path).resolve()
        connection = sqlite3.connect(
            f"file:{resolved.as_posix()}?mode=rw", uri=True, timeout=5
        )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    imdb_path = Path(database_path).resolve().with_name("imdb.db")
    if not imdb_path.is_file():
        imdb_path = Path(__file__).resolve().with_name("imdb.db")
    if not imdb_path.is_file():
        connection.close()
        raise FileNotFoundError(f"IMDb database not found: {imdb_path}")
    connection.execute(
        "ATTACH DATABASE ? AS imdb",
        (f"file:{imdb_path.as_posix()}?mode=ro",),
    )
    return connection


def _primary_media_id(db, media_type: str, tmdb_id: int):
    row = db.execute(
        "SELECT media_id FROM media WHERE media_type = ? AND tmdb_id = ?",
        (media_type, int(tmdb_id)),
    ).fetchone()
    return int(row["media_id"]) if row else None


def _require_primary_media_id(db, media_type: str, tmdb_id: int):
    media_id = _primary_media_id(db, media_type, tmdb_id)
    if media_id is None:
        raise ValueError("媒体不存在。")
    return media_id


def _upsert_media(
    db,
    *,
    media_type: str,
    tmdb_id: int,
    imdb_id: str | None,
    display_title: str,
    parent_media_id: int | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
):
    imdb_id = imdb_id or None
    if media_type in ("movie", "series") and imdb_id is None:
        raise ValueError("IMDb 主数据中缺少该电影或剧集的 IMDb ID。")
    if imdb_id and db.execute(
        "SELECT 1 FROM imdb.title_basics WHERE tconst = ?", (imdb_id,)
    ).fetchone() is None:
        raise ValueError(f"本地 IMDb 数据库中不存在 {imdb_id}。")
    db.execute(
        """
        INSERT INTO media (
                    media_type, imdb_id, tmdb_id, parent_media_id,
                    season_number, episode_number, display_title, match_status
               )
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (media_type, tmdb_id)
        DO UPDATE SET imdb_id = COALESCE(excluded.imdb_id, media.imdb_id),
                      parent_media_id = excluded.parent_media_id,
                      season_number = excluded.season_number,
                      episode_number = excluded.episode_number,
                      display_title = excluded.display_title,
                      match_status = CASE
                          WHEN COALESCE(excluded.imdb_id, media.imdb_id) IS NULL
                          THEN 'provisional' ELSE 'matched' END,
                      updated_at = CURRENT_TIMESTAMP
        """,
        (
            media_type, imdb_id, int(tmdb_id), parent_media_id, season_number,
            episode_number, display_title, "matched" if imdb_id else "provisional",
        ),
    )
    return _require_primary_media_id(db, media_type, tmdb_id)


def _upsert_tmdb_metadata(db, media_id: int, **metadata):
    db.execute(
        """
        INSERT INTO tmdb_metadata (
                    media_id, original_language, overview, tagline, homepage,
                    status, air_date, synced_at
               )
             VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (media_id)
        DO UPDATE SET original_language = excluded.original_language,
                      overview = excluded.overview,
                      tagline = excluded.tagline,
                      homepage = excluded.homepage,
                      status = excluded.status,
                      air_date = excluded.air_date,
                      synced_at = CURRENT_TIMESTAMP
        """,
        (
            media_id, metadata.get("original_language"), metadata.get("overview"),
            metadata.get("tagline"), metadata.get("homepage"),
            metadata.get("status"), metadata.get("air_date") or None,
        ),
    )


def _find_episode_imdb_id(db, series_imdb_id, season_number, episode_number):
    row = db.execute(
        """
        SELECT tconst FROM imdb.title_episode
         WHERE parentTconst = ? AND seasonNumber = ? AND episodeNumber = ?
        """,
        (series_imdb_id, season_number, episode_number),
    ).fetchone()
    return row["tconst"] if row else None


def _is_watch_completed(position_ms: int, duration_ms: int):
    if duration_ms <= 0:
        return False
    return (
        position_ms / duration_ms >= WATCH_COMPLETE_RATIO
        or max(duration_ms - position_ms, 0) <= WATCH_COMPLETE_REMAINING_MS
    )


def _format_time_label(position_ms: int):
    total_seconds = max(int(position_ms or 0), 0) // 1000
    total_minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(total_minutes, 60)
    return (
        f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if hours else f"{minutes:02d}:{seconds:02d}"
    )


def _add_progress_fields(item: dict):
    position_ms = max(int(item.get("position_ms") or 0), 0)
    duration_ms = max(int(item.get("duration_ms") or 0), 0)
    if duration_ms:
        position_ms = min(position_ms, duration_ms)
    completed = _is_watch_completed(position_ms, duration_ms)
    item.update(
        position_ms=position_ms,
        duration_ms=duration_ms,
        position_label=_format_time_label(position_ms) if position_ms else "",
        duration_label=_format_time_label(duration_ms) if duration_ms else "",
        progress=min(round(position_ms * 100 / duration_ms), 100) if duration_ms else 0,
        is_completed=completed,
        resume_position_ms=(
            position_ms if position_ms >= WATCH_RESUME_MIN_MS and not completed else 0
        ),
    )
    return item


def get_media_source(database_path, media_type, source_id):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    storage_type = "movie" if media_type == "movie" else "episode"
    with closing(_connect(database_path)) as db:
        row = db.execute(
            """
            SELECT source.source_id, item.tmdb_id AS media_id, source.alist_path
              FROM media_source AS source JOIN media AS item
                ON item.media_id = source.media_id
             WHERE source.source_id = ? AND item.media_type = ?
            """,
            (source_id, storage_type),
        ).fetchone()
    return dict(row) if row else None


def get_watch_progress(database_path, media_type, media_id):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    storage_type = "movie" if media_type == "movie" else "episode"
    with closing(_connect(database_path)) as db:
        row = db.execute(
            """
            SELECT progress.position_ms, progress.duration_ms, progress.updated_at
              FROM watch_progress AS progress JOIN media AS item
                ON item.media_id = progress.media_id
             WHERE item.media_type = ? AND item.tmdb_id = ?
            """,
            (storage_type, media_id),
        ).fetchone()
    return _add_progress_fields(dict(row) if row else {})


def save_watch_progress(database_path, media_type, media_id, position_ms, duration_ms):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    storage_type = "movie" if media_type == "movie" else "episode"
    position_ms, duration_ms = max(int(position_ms), 0), max(int(duration_ms), 0)
    if duration_ms:
        position_ms = min(position_ms, duration_ms)
    with closing(_connect(database_path)) as db, db:
        primary_id = _require_primary_media_id(db, storage_type, media_id)
        db.execute(
            """
            INSERT INTO watch_progress (media_id, position_ms, duration_ms, updated_at)
                 VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (media_id)
            DO UPDATE SET position_ms = excluded.position_ms,
                          duration_ms = excluded.duration_ms,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (primary_id, position_ms, duration_ms),
        )


def _update_source_media_metadata(db, alist_path, metadata):
    values = {column: metadata.get(column) for column in SOURCE_MEDIA_COLUMNS}
    values["alist_path"] = alist_path
    cursor = db.execute(
        """
        UPDATE media_source
           SET media_duration_ms = :media_duration_ms,
               file_size_bytes = :file_size_bytes,
               bit_rate = :bit_rate,
               container_format = :container_format,
               video_codec = :video_codec,
               video_width = :video_width,
               video_height = :video_height,
               video_frame_rate = :video_frame_rate,
               video_pixel_format = :video_pixel_format,
               video_dynamic_range = :video_dynamic_range,
               audio_codec = :audio_codec,
               audio_channels = :audio_channels,
               audio_channel_layout = :audio_channel_layout,
               probed_at = CURRENT_TIMESTAMP
         WHERE alist_path = :alist_path
        """,
        values,
    )
    if cursor.rowcount == 0:
        raise ValueError("媒体来源不存在。")


def _upsert_source(db, media_type, owner_id, alist_path, metadata=None):
    storage_type = "movie" if media_type == "movie" else "episode"
    primary_id = _require_primary_media_id(db, storage_type, owner_id)
    existing = db.execute(
        "SELECT media_id FROM media_source WHERE alist_path = ?", (alist_path,)
    ).fetchone()
    if existing and int(existing["media_id"]) != primary_id:
        raise ValueError("该 AList 文件已关联至其他媒体。")
    db.execute(
        """
        INSERT INTO media_source (media_id, alist_path) VALUES (?, ?)
        ON CONFLICT (alist_path) DO UPDATE SET media_id = excluded.media_id
        """,
        (primary_id, alist_path),
    )
    if metadata is not None:
        _update_source_media_metadata(db, alist_path, metadata)


def save_source_media_metadata(database_path, media_type, source_id, metadata):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    storage_type = "movie" if media_type == "movie" else "episode"
    with closing(_connect(database_path)) as db, db:
        row = db.execute(
            """
            SELECT source.alist_path FROM media_source AS source JOIN media AS item
              ON item.media_id = source.media_id
             WHERE source.source_id = ? AND item.media_type = ?
            """,
            (source_id, storage_type),
        ).fetchone()
        if row is None:
            raise ValueError("媒体来源不存在。")
        _update_source_media_metadata(db, row["alist_path"], metadata)


def get_sources_missing_media_metadata(database_path):
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT CASE item.media_type
                       WHEN 'episode' THEN 'tv'
                       ELSE item.media_type
                   END AS media_type,
                   source.source_id, source.alist_path
              FROM media_source AS source JOIN media AS item
                ON item.media_id = source.media_id
             WHERE source.probed_at IS NULL ORDER BY source.source_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_source_paths(database_path):
    with closing(_connect(database_path)) as db:
        return {row[0] for row in db.execute("SELECT alist_path FROM media_source")}


def has_movie(database_path, movie_id):
    with closing(_connect(database_path)) as db:
        return _primary_media_id(db, "movie", movie_id) is not None


def find_tv_episode_id(database_path, series_id, season_number, episode_number):
    with closing(_connect(database_path)) as db:
        row = db.execute(
            """
            SELECT episode.tmdb_id FROM media AS episode JOIN media AS series
              ON series.media_id = episode.parent_media_id
             WHERE episode.media_type = 'episode' AND series.tmdb_id = ?
               AND episode.season_number = ? AND episode.episode_number = ?
            """,
            (series_id, season_number, episode_number),
        ).fetchone()
    return int(row[0]) if row else None


def add_movie_source(database_path, movie_id, alist_path):
    with closing(_connect(database_path)) as db, db:
        _upsert_source(db, "movie", movie_id, alist_path)


def add_tv_episode_source(database_path, episode_id, alist_path):
    with closing(_connect(database_path)) as db, db:
        _upsert_source(db, "tv", episode_id, alist_path)


def find_episodes_missing_stills(database_path):
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT series.tmdb_id AS series_id, episode.tmdb_id AS episode_id,
                   episode.season_number, episode.episode_number
              FROM media AS episode JOIN media AS series
                ON series.media_id = episode.parent_media_id
             WHERE episode.media_type = 'episode'
               AND EXISTS (SELECT 1 FROM media_source WHERE media_id = episode.media_id)
               AND NOT EXISTS (
                   SELECT 1 FROM media_image WHERE media_id = episode.media_id
                    AND image_type = 'still'
               )
             ORDER BY series.tmdb_id, episode.season_number, episode.episode_number
            """
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_tv_episode_still(database_path, episode_id, local_path):
    with closing(_connect(database_path)) as db, db:
        primary_id = _require_primary_media_id(db, "episode", episode_id)
        db.execute(
            """
            INSERT INTO media_image (media_id, image_type, image_position, local_path)
                 VALUES (?, 'still', 1, ?)
            ON CONFLICT (media_id, image_type, image_position)
            DO UPDATE SET local_path = excluded.local_path
            """,
            (primary_id, local_path),
        )


def _replace_media_images(db, media_type, owner_id, images):
    storage_type = "movie" if media_type == "movie" else "series"
    primary_id = _require_primary_media_id(db, storage_type, owner_id)
    preferred = {
        row[0]: row[1] for row in db.execute(
            """
            SELECT image_type, local_path FROM media_image
             WHERE media_id = ? AND image_type IN ('poster', 'logo', 'backdrop')
               AND image_position = 1
            """,
            (primary_id,),
        )
    }
    db.execute(
        """
        DELETE FROM media_image
         WHERE media_id = ?
           AND image_type IN ('poster', 'logo', 'backdrop')
        """,
        (primary_id,),
    )
    for external_type, storage_type_name in IMAGE_TYPE_TO_STORAGE.items():
        selected = sorted(
            (image for image in images if image["image_type"] == external_type),
            key=lambda image: (
                image["local_path"] != preferred.get(storage_type_name),
                image["image_position"],
            ),
        )
        for position, image in enumerate(selected, 1):
            db.execute(
                """
                INSERT INTO media_image (
                    media_id, image_type, image_position, remote_path, local_path
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (primary_id, storage_type_name, position,
                 image.get("image_url"), image["local_path"]),
            )


def save_movie(database_path, details, images, alist_path, source_metadata=None):
    with closing(_connect(database_path)) as db, db:
        movie_id = int(details["id"])
        primary_id = _upsert_media(
            db, media_type="movie", tmdb_id=movie_id,
            imdb_id=details.get("imdb_id"),
            display_title=details.get("title") or details.get("original_title") or "",
        )
        _upsert_tmdb_metadata(
            db, primary_id, overview=details.get("overview"),
            tagline=details.get("tagline"), homepage=details.get("homepage"),
            status=details.get("status"),
        )
        _replace_media_images(db, "movie", movie_id, images)
        _upsert_source(db, "movie", movie_id, alist_path, source_metadata)


def save_tv_episode(
    database_path, series_details, season_details, episode_number, alist_path,
    series_images=None, episode_still=None, source_metadata=None,
):
    with closing(_connect(database_path)) as db, db:
        series_tmdb_id = int(series_details["id"])
        series_imdb_id = series_details.get("imdb_id")
        series_media_id = _upsert_media(
            db, media_type="series", tmdb_id=series_tmdb_id,
            imdb_id=series_imdb_id,
            display_title=series_details.get("name") or series_details.get("original_name") or "",
        )
        _upsert_tmdb_metadata(
            db, series_media_id,
            original_language=series_details.get("original_language"),
            overview=series_details.get("overview"), tagline=series_details.get("tagline"),
            homepage=series_details.get("homepage"), status=series_details.get("status"),
        )
        season_number = int(season_details["season_number"])
        selected_episode_id = None
        for episode in season_details.get("episodes", []):
            current_number = int(episode["episode_number"])
            episode_media_id = _upsert_media(
                db, media_type="episode", tmdb_id=int(episode["id"]),
                imdb_id=_find_episode_imdb_id(
                    db, series_imdb_id, season_number, current_number
                ),
                display_title=episode.get("name") or f"第 {current_number} 集",
                parent_media_id=series_media_id, season_number=season_number,
                episode_number=current_number,
            )
            _upsert_tmdb_metadata(
                db, episode_media_id, overview=episode.get("overview"),
                air_date=episode.get("air_date"),
            )
            if current_number == episode_number:
                selected_episode_id = int(episode["id"])
        if selected_episode_id is None:
            raise ValueError("TMDB 中未找到指定季和集数。")
        _upsert_source(db, "tv", selected_episode_id, alist_path, source_metadata)
        if series_images is not None:
            _replace_media_images(db, "tv", series_tmdb_id, series_images)
        if episode_still is not None:
            _upsert_episode_still(db, selected_episode_id, episode_still)


def _upsert_episode_still(db, selected_episode_id, episode_still):
    if int(episode_still["episode_id"]) != selected_episode_id:
        raise ValueError("单集剧照与所选 Episode 不一致。")
    primary_id = _require_primary_media_id(db, "episode", selected_episode_id)
    db.execute(
        """
        INSERT INTO media_image (
            media_id, image_type, image_position, remote_path, local_path
        ) VALUES (?, 'still', 1, ?, ?)
        ON CONFLICT (media_id, image_type, image_position)
        DO UPDATE SET remote_path = excluded.remote_path, local_path = excluded.local_path
        """,
        (primary_id, episode_still.get("image_url") or episode_still.get("still_path"),
         episode_still["local_path"]),
    )


def get_tv_series_images(database_path, series_id):
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT CASE image.image_type WHEN 'poster' THEN 'posters'
                       WHEN 'logo' THEN 'logos' WHEN 'backdrop' THEN 'backdrops' END AS image_type,
                   image.image_position, image.remote_path AS image_url, image.local_path
              FROM media_image AS image JOIN media AS item ON item.media_id = image.media_id
             WHERE item.media_type = 'series' AND item.tmdb_id = ?
               AND image.image_type IN ('poster', 'logo', 'backdrop')
             ORDER BY image.image_type, image.image_position
            """,
            (series_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_primary_media_image(
    database_path,
    media_type,
    media_id,
    image_type,
    image_position,
):
    if media_type not in ("movie", "tv") or image_type not in IMAGE_TYPE_TO_STORAGE:
        raise ValueError("不支持的媒体或图片类型。")
    storage_type = "movie" if media_type == "movie" else "series"
    stored_image_type = IMAGE_TYPE_TO_STORAGE[image_type]
    with closing(_connect(database_path)) as db, db:
        primary_id = _require_primary_media_id(db, storage_type, media_id)
        exists = db.execute(
            """
            SELECT 1
              FROM media_image
             WHERE media_id = ?
               AND image_type = ?
               AND image_position = ?
            """,
            (primary_id, stored_image_type, image_position),
        ).fetchone()
        if not exists:
            raise ValueError("指定图片不存在。")
        if image_position == 1:
            return
        temporary = db.execute(
            """
            SELECT COALESCE(MAX(image_position), 0) + 1
              FROM media_image
             WHERE media_id = ?
               AND image_type = ?
            """,
            (primary_id, stored_image_type),
        ).fetchone()[0]
        swaps = ((image_position, temporary), (1, image_position), (temporary, 1))
        for old, new in swaps:
            db.execute(
                """
                UPDATE media_image
                   SET image_position = ?
                 WHERE media_id = ?
                   AND image_type = ?
                   AND image_position = ?
                """,
                (new, primary_id, stored_image_type, old),
            )

### 这是否太长了呢？我完全看不下去这是做什么的
LIBRARY_ITEMS_SQL = """
WITH ranked_source AS (
    SELECT CASE linked.media_type WHEN 'episode' THEN linked.parent_media_id
                                  ELSE linked.media_id END AS owner_media_id,
           source.source_id, source.alist_path, linked.season_number,
           linked.episode_number, linked.display_title AS episode_name,
           ROW_NUMBER() OVER (
               PARTITION BY CASE linked.media_type WHEN 'episode' THEN linked.parent_media_id
                                                        ELSE linked.media_id END
                   ORDER BY source.source_id DESC
           ) AS source_rank
      FROM media_source AS source JOIN media AS linked
        ON linked.media_id = source.media_id
)
SELECT item.media_id AS internal_media_id,
       CASE item.media_type WHEN 'series' THEN 'tv' ELSE item.media_type END AS media_type,
       item.tmdb_id AS media_id, item.display_title AS name,
       title.originalTitle AS original_name, CAST(title.startYear AS TEXT) AS year,
       CAST(title.startYear AS TEXT) AS release_date,
       rating.averageRating AS imdb_rating, rating.numVotes AS imdb_vote_count,
       metadata.overview, metadata.status AS media_status,
       metadata.original_language, latest.source_id, latest.alist_path,
       latest.season_number, latest.episode_number, latest.episode_name,
       title.genres AS genres_text,
       (SELECT COUNT(*) FROM media AS episode
         WHERE episode.parent_media_id = item.media_id) AS episode_count,
       (SELECT COUNT(DISTINCT episode.season_number) FROM media AS episode
         WHERE episode.parent_media_id = item.media_id) AS season_count,
       (SELECT COUNT(*) FROM media_source AS source JOIN media AS linked
          ON linked.media_id = source.media_id
         WHERE linked.media_id = item.media_id
            OR linked.parent_media_id = item.media_id) AS source_count,
       (SELECT COUNT(*) FROM media_image AS image
         WHERE image.media_id = item.media_id AND image.image_type <> 'still') AS image_count,
       (SELECT image.local_path FROM media_image AS image
         WHERE image.media_id = item.media_id AND image.image_type = 'poster'
         ORDER BY image.image_position LIMIT 1) AS poster_path,
       (SELECT image.local_path FROM media_image AS image
         WHERE image.media_id = item.media_id AND image.image_type = 'backdrop'
         ORDER BY image.image_position LIMIT 1) AS backdrop_path,
       (SELECT image.local_path FROM media_image AS image
         WHERE image.media_id = item.media_id AND image.image_type = 'logo'
         ORDER BY image.image_position LIMIT 1) AS logo_path
  FROM media AS item
  LEFT JOIN tmdb_metadata AS metadata ON metadata.media_id = item.media_id
  LEFT JOIN imdb.title_basics AS title ON title.tconst = item.imdb_id
  LEFT JOIN imdb.title_ratings AS rating ON rating.tconst = item.imdb_id
  LEFT JOIN ranked_source AS latest ON latest.owner_media_id = item.media_id
   AND latest.source_rank = 1
 WHERE item.media_type IN ('movie', 'series')
"""


def _split_genres(value):
    return [genre.strip() for genre in (value or "").split(",") if genre.strip()]


def _library_items(db, *, linked_only=True):
    items = []
    for row in db.execute(LIBRARY_ITEMS_SQL).fetchall():
        item = dict(row)
        item["genres"] = _split_genres(item.get("genres_text"))
        if not linked_only or item.get("source_id") is not None:
            items.append(item)
    return items


def _genre_catalog(items):
    names = sorted(
        {genre for item in items for genre in item.get("genres", [])},
        key=str.casefold,
    )
    return [
        {"genre_id": index, "genre_name": name}
        for index, name in enumerate(names, 1)
    ]


def _genre_features(items, media_type):
    typed = [item for item in items if item["media_type"] == media_type]
    features = []
    for genre in _genre_catalog(typed):
        candidates = [item for item in typed if genre["genre_name"] in item["genres"]]
        if not candidates:
            continue
        selected = max(
            candidates,
            key=lambda item: (item.get("imdb_rating") or 0, item["media_id"]),
        )
        features.append(
            {
                **genre,
                "media_id": selected["media_id"],
                "media_name": selected["name"],
                "backdrop_path": selected.get("backdrop_path"),
            }
        )
    return features


def get_filtered_library_items(
    database_path, media_type="all", genre_id=None, language_code=None,
    year=None, min_rating=None,
):
    if media_type not in ("all", "movie", "tv"):
        raise ValueError("媒体类型无效。")
    with closing(_connect(database_path)) as db:
        items = _library_items(db)
    typed = [item for item in items if media_type == "all" or item["media_type"] == media_type]
    selected_genre = next(
        (genre["genre_name"] for genre in _genre_catalog(typed)
         if genre["genre_id"] == genre_id),
        None,
    )
    filtered = [
        item for item in typed
        if (selected_genre is None or selected_genre in item["genres"])
        and (language_code is None or item.get("original_language") == language_code)
        and (year is None or item.get("year") == year)
        and (min_rating is None or (item.get("imdb_rating") or 0) >= min_rating)
    ]
    return sorted(
        filtered,
        key=lambda item: (item.get("source_id") or 0, item["media_id"]),
        reverse=True,
    )


def get_library_filter_options(database_path, media_type="all"):
    if media_type not in ("all", "movie", "tv"):
        raise ValueError("媒体类型无效。")
    with closing(_connect(database_path)) as db:
        items = [
            item for item in _library_items(db)
            if media_type == "all" or item["media_type"] == media_type
        ]
    languages = sorted(
        {item["original_language"] for item in items if item.get("original_language")}
    )
    return {
        "genres": _genre_catalog(items),
        "languages": [
            {"language_code": code, "language_name": code} for code in languages
        ],
        "years": sorted(
            {item["year"] for item in items if item.get("year")}, reverse=True
        ),
    }


def _continue_watching_items(db, item_limit):
    rows = db.execute(
        """
        SELECT CASE owner.media_type
                   WHEN 'series' THEN 'tv'
                   ELSE owner.media_type
               END AS media_type,
               owner.tmdb_id AS media_id, owner.display_title AS name,
               owner_title.originalTitle AS original_name,
               CAST(owner_title.startYear AS TEXT) AS year,
               CAST(owner_title.startYear AS TEXT) AS release_date,
               owner_rating.averageRating AS imdb_rating,
               owner_metadata.overview, source.source_id,
               linked.season_number, linked.episode_number,
               linked.display_title AS episode_name,
               progress.position_ms, progress.duration_ms, progress.updated_at,
               (SELECT local_path FROM media_image WHERE media_id = owner.media_id
                 AND image_type = 'poster' ORDER BY image_position LIMIT 1) AS poster_path,
               (SELECT local_path FROM media_image WHERE media_id = owner.media_id
                 AND image_type = 'backdrop' ORDER BY image_position LIMIT 1) AS backdrop_path,
               (SELECT local_path FROM media_image WHERE media_id = owner.media_id
                 AND image_type = 'logo' ORDER BY image_position LIMIT 1) AS logo_path
          FROM watch_progress AS progress JOIN media AS linked
            ON linked.media_id = progress.media_id
          JOIN media AS owner ON owner.media_id = CASE linked.media_type
              WHEN 'episode' THEN linked.parent_media_id ELSE linked.media_id END
          JOIN media_source AS source ON source.media_id = linked.media_id
           AND source.source_id = (
               SELECT MAX(candidate.source_id) FROM media_source AS candidate
                WHERE candidate.media_id = linked.media_id
           )
          LEFT JOIN tmdb_metadata AS owner_metadata ON owner_metadata.media_id = owner.media_id
          LEFT JOIN imdb.title_basics AS owner_title ON owner_title.tconst = owner.imdb_id
          LEFT JOIN imdb.title_ratings AS owner_rating ON owner_rating.tconst = owner.imdb_id
         WHERE progress.position_ms >= ? AND progress.duration_ms > 0
         ORDER BY progress.updated_at DESC LIMIT ?
        """,
        (WATCH_RESUME_MIN_MS, item_limit),
    ).fetchall()
    return [_add_progress_fields(dict(row)) for row in rows]


def _favorite_folders(db):
    rows = db.execute(
        """
        SELECT folder.folder_id, folder.folder_name AS name, folder.folder_type,
               folder.icon_name AS icon, folder.is_system,
               COUNT(favorite.favorite_item_id) AS count
          FROM favorite_folder AS folder LEFT JOIN favorite_item AS favorite
            ON favorite.folder_id = folder.folder_id
         GROUP BY folder.folder_id ORDER BY folder.folder_position
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _started_media_ids(db):
    return {
        row[0] for row in db.execute(
            """
            SELECT DISTINCT CASE item.media_type WHEN 'episode' THEN item.parent_media_id
                                                  ELSE item.media_id END
              FROM watch_progress AS progress JOIN media AS item
                ON item.media_id = progress.media_id
             WHERE progress.position_ms > 0
            """
        )
    }


def get_library_page_items(database_path, section_name):
    if section_name not in ("recent_added", "unwatched", "top_rated_movies"):
        raise ValueError("未知媒体库分区。")
    with closing(_connect(database_path)) as db:
        items = _library_items(db)
        if section_name == "unwatched":
            started = _started_media_ids(db)
            items = [item for item in items if item["internal_media_id"] not in started]
        elif section_name == "top_rated_movies":
            return sorted(
                (item for item in items if item["media_type"] == "movie"),
                key=lambda item: (item.get("imdb_rating") or 0, item["media_id"]),
                reverse=True,
            )
    return sorted(
        items,
        key=lambda item: (item.get("source_id") or 0, item["media_id"]),
        reverse=True,
    )


def get_homepage_data(database_path, item_limit=12):
    with closing(_connect(database_path)) as db:
        items = _library_items(db)
        started = _started_media_ids(db)
        recent = sorted(
            items,
            key=lambda item: (item.get("source_id") or 0, item["media_id"]),
            reverse=True,
        )
        top_movies = sorted(
            (item for item in items if item["media_type"] == "movie"),
            key=lambda item: (item.get("imdb_rating") or 0, item["media_id"]),
            reverse=True,
        )
        return {
            "continue_watching": _continue_watching_items(db, item_limit),
            "favorite_folders": _favorite_folders(db),
            "recent_added": recent[:item_limit],
            "genre_features": _genre_features(items, "movie")[:item_limit],
            "tv_genre_features": _genre_features(items, "tv")[:item_limit],
            "unwatched": [
                item for item in recent if item["internal_media_id"] not in started
            ][:item_limit],
            "top_rated_movies": top_movies[:item_limit],
            "media_count": len(items),
            "movie_count": sum(item["media_type"] == "movie" for item in items),
            "tv_count": sum(item["media_type"] == "tv" for item in items),
        }


def get_favorite_page_data(database_path, folder_id=None):
    with closing(_connect(database_path)) as db:
        folders = _favorite_folders(db)
        selected = next(
            (folder for folder in folders if folder["folder_id"] == folder_id), None
        )
        if folder_id is not None and selected is None:
            raise ValueError("收藏夹不存在。")
        items_by_id = {
            item["internal_media_id"]: item
            for item in _library_items(db, linked_only=False)
        }
        rows = db.execute(
            """
            SELECT media_id FROM favorite_item
             WHERE (? IS NULL OR folder_id = ?)
             ORDER BY added_at DESC, favorite_item_id DESC
            """,
            (folder_id, folder_id),
        ).fetchall()
        items = [
            items_by_id[row[0]] for row in rows if row[0] in items_by_id
        ]
    return {"folders": folders, "selected_folder": selected, "items": items}


def is_media_favorite(database_path, media_type, media_id):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    storage_type, folder_id = (
        ("movie", 1) if media_type == "movie" else ("series", 2)
    )
    with closing(_connect(database_path)) as db:
        row = db.execute(
            """
            SELECT 1 FROM favorite_item AS favorite JOIN media AS item
              ON item.media_id = favorite.media_id
             WHERE favorite.folder_id = ? AND item.media_type = ? AND item.tmdb_id = ?
            """,
            (folder_id, storage_type, media_id),
        ).fetchone()
    return row is not None


def toggle_media_favorite(database_path, media_type, media_id):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    storage_type, folder_id = (
        ("movie", 1) if media_type == "movie" else ("series", 2)
    )
    with closing(_connect(database_path)) as db, db:
        primary_id = _require_primary_media_id(db, storage_type, media_id)
        existing = db.execute(
            "SELECT favorite_item_id FROM favorite_item WHERE folder_id = ? AND media_id = ?",
            (folder_id, primary_id),
        ).fetchone()
        if existing:
            db.execute(
                "DELETE FROM favorite_item WHERE favorite_item_id = ?", (existing[0],)
            )
            return False
        db.execute(
            "INSERT INTO favorite_item (folder_id, media_id) VALUES (?, ?)",
            (folder_id, primary_id),
        )
        return True


def _primary_image(db, media_id, image_type):
    row = db.execute(
        """
        SELECT local_path FROM media_image
         WHERE media_id = ? AND image_type = ?
         ORDER BY image_position LIMIT 1
        """,
        (media_id, image_type),
    ).fetchone()
    return row[0] if row else None


def _source_rows(db, media_id):
    return db.execute(
        """
        SELECT source.source_id, source.alist_path, source.media_duration_ms,
               source.file_size_bytes, source.bit_rate, source.container_format,
               source.video_codec, source.video_width, source.video_height,
               source.video_frame_rate, source.video_pixel_format,
               source.video_dynamic_range, source.audio_codec,
               source.audio_channels, source.audio_channel_layout, source.probed_at,
               progress.position_ms, progress.duration_ms, progress.updated_at
          FROM media_source AS source LEFT JOIN watch_progress AS progress
            ON progress.media_id = source.media_id
         WHERE source.media_id = ? ORDER BY source.source_id
        """,
        (media_id,),
    ).fetchall()


def _group_episodes(rows):
    seasons, episodes, sources = {}, {}, []
    for raw in rows:
        row = dict(raw)
        number = row["season_number"]
        season = seasons.setdefault(
            number,
            {
                "season_id": number,
                "season_number": number,
                "season_name": "特别篇" if number == 0 else f"第 {number} 季",
                "overview": None,
                "air_date": row.get("air_date"),
                "episode_count": 0,
                "imdb_rating": None,
                "episodes": [],
                "_ratings": [],
            },
        )
        if row.get("air_date") and (
            season["air_date"] is None or row["air_date"] < season["air_date"]
        ):
            season["air_date"] = row["air_date"]
        episode = episodes.get(row["episode_id"])
        if episode is None:
            episode = {
                key: row.get(key) for key in (
                    "episode_id", "episode_number", "episode_name", "runtime",
                    "air_date", "overview", "imdb_id", "imdb_rating",
                    "imdb_vote_count", "still_path", "position_ms", "duration_ms",
                    "updated_at",
                )
            }
            episode.update(season_number=number, sources=[])
            _add_progress_fields(episode)
            episodes[row["episode_id"]] = episode
            season["episodes"].append(episode)
            season["episode_count"] += 1
            if row.get("imdb_rating") is not None:
                season["_ratings"].append(float(row["imdb_rating"]))
        if row.get("source_id") is not None:
            source = {
                key: row.get(key) for key in (
                    "source_id", "alist_path", "media_duration_ms", "file_size_bytes",
                    "bit_rate", "container_format", "video_codec", "video_width",
                    "video_height", "video_frame_rate", "video_pixel_format",
                    "video_dynamic_range", "audio_codec", "audio_channels",
                    "audio_channel_layout", "probed_at", "position_ms", "duration_ms",
                    "updated_at",
                )
            }
            source.update(
                episode_id=row["episode_id"],
                episode_number=row["episode_number"],
                episode_name=row.get("episode_name"),
                season_number=number,
            )
            _add_progress_fields(source)
            episode["sources"].append(source)
            sources.append(source)
    result = []
    for number in sorted(seasons):
        season = seasons[number]
        ratings = season.pop("_ratings")
        season["imdb_rating"] = round(sum(ratings) / len(ratings), 1) if ratings else None
        result.append(season)
    return result, sources


def get_media_detail(database_path, media_type, media_id):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    storage_type = "movie" if media_type == "movie" else "series"
    with closing(_connect(database_path)) as db:
        row = db.execute(
            """
            SELECT CASE item.media_type
                       WHEN 'series' THEN 'tv'
                       ELSE item.media_type
                   END AS media_type,
                   item.media_id AS internal_media_id, item.tmdb_id AS media_id,
                   item.display_title AS name, title.originalTitle AS original_name,
                   CAST(title.startYear AS TEXT) AS year,
                   CAST(title.startYear AS TEXT) AS release_date,
                   rating.averageRating AS imdb_rating,
                   rating.numVotes AS imdb_vote_count, title.runtimeMinutes AS runtime,
                   metadata.overview, metadata.tagline, metadata.status AS media_status,
                   metadata.original_language, metadata.homepage, item.imdb_id,
                   title.genres AS genres_text, CAST(title.endYear AS TEXT) AS last_air_date
              FROM media AS item LEFT JOIN tmdb_metadata AS metadata
                ON metadata.media_id = item.media_id
              LEFT JOIN imdb.title_basics AS title ON title.tconst = item.imdb_id
              LEFT JOIN imdb.title_ratings AS rating ON rating.tconst = item.imdb_id
             WHERE item.media_type = ? AND item.tmdb_id = ?
            """,
            (storage_type, media_id),
        ).fetchone()
        if row is None:
            return None
        detail = dict(row)
        internal_id = detail.pop("internal_media_id")
        detail["genres"] = _split_genres(detail.pop("genres_text", None))
        detail.update(
            poster_path=_primary_image(db, internal_id, "poster"),
            backdrop_path=_primary_image(db, internal_id, "backdrop"),
            logo_path=_primary_image(db, internal_id, "logo"),
            age_rating=None,
            sources=[],
            seasons=[],
        )
        if media_type == "movie":
            detail["original_language"] = None
            detail["sources"] = [
                _add_progress_fields(dict(source)) for source in _source_rows(db, internal_id)
            ]
        else:
            episode_rows = db.execute(
                """
                SELECT episode.tmdb_id AS episode_id, episode.season_number,
                       episode.episode_number, episode.display_title AS episode_name,
                       metadata.overview, metadata.air_date, title.runtimeMinutes AS runtime,
                       episode.imdb_id, rating.averageRating AS imdb_rating,
                       rating.numVotes AS imdb_vote_count, image.local_path AS still_path,
                       source.source_id, source.alist_path, source.media_duration_ms,
                       source.file_size_bytes, source.bit_rate, source.container_format,
                       source.video_codec, source.video_width, source.video_height,
                       source.video_frame_rate, source.video_pixel_format,
                       source.video_dynamic_range, source.audio_codec,
                       source.audio_channels, source.audio_channel_layout, source.probed_at,
                       progress.position_ms, progress.duration_ms, progress.updated_at
                  FROM media AS episode LEFT JOIN tmdb_metadata AS metadata
                    ON metadata.media_id = episode.media_id
                  LEFT JOIN imdb.title_basics AS title ON title.tconst = episode.imdb_id
                  LEFT JOIN imdb.title_ratings AS rating ON rating.tconst = episode.imdb_id
                  LEFT JOIN media_image AS image ON image.media_id = episode.media_id
                   AND image.image_type = 'still' AND image.image_position = 1
                  LEFT JOIN media_source AS source ON source.media_id = episode.media_id
                  LEFT JOIN watch_progress AS progress ON progress.media_id = episode.media_id
                 WHERE episode.parent_media_id = ? AND episode.media_type = 'episode'
                 ORDER BY episode.season_number, episode.episode_number, source.source_id
                """,
                (internal_id,),
            ).fetchall()
            detail["seasons"], detail["sources"] = _group_episodes(episode_rows)
            detail["number_of_seasons"] = len(detail["seasons"])
            detail["number_of_episodes"] = sum(
                len(season["episodes"]) for season in detail["seasons"]
            )
            detail["in_production"] = detail.get("last_air_date") is None
    return detail


def get_media_overview_data(database_path, query=None, media_type="all"):
    if media_type not in ("all", "movie", "tv"):
        media_type = "all"
    with closing(_connect(database_path)) as db:
        items = _library_items(db, linked_only=False)
        stats = {
            "movie_count": db.execute(
                "SELECT COUNT(*) FROM media WHERE media_type = 'movie'"
            ).fetchone()[0],
            "tv_count": db.execute(
                "SELECT COUNT(*) FROM media WHERE media_type = 'series'"
            ).fetchone()[0],
            "season_count": db.execute(
                """
                SELECT COUNT(*)
                  FROM (
                    SELECT parent_media_id, season_number
                      FROM media
                     WHERE media_type = 'episode'
                     GROUP BY parent_media_id, season_number
                  )
                """
            ).fetchone()[0],
            "episode_count": db.execute(
                "SELECT COUNT(*) FROM media WHERE media_type = 'episode'"
            ).fetchone()[0],
            "source_count": db.execute("SELECT COUNT(*) FROM media_source").fetchone()[0],
            "image_count": db.execute("SELECT COUNT(*) FROM media_image").fetchone()[0],
        }
        stats["media_count"] = stats["movie_count"] + stats["tv_count"]
        image_rows = db.execute(
            """
            SELECT item.media_id,
                   CASE image.image_type WHEN 'poster' THEN 'posters'
                       WHEN 'logo' THEN 'logos' WHEN 'backdrop' THEN 'backdrops' END AS image_type,
                   image.image_position, image.local_path
              FROM media_image AS image JOIN media AS item ON item.media_id = image.media_id
             WHERE item.media_type IN ('movie', 'series')
               AND image.image_type IN ('poster', 'logo', 'backdrop')
             ORDER BY item.media_id, image.image_type, image.image_position
            """
        ).fetchall()
    options = {}
    for row in image_rows:
        target = options.setdefault(
            row["media_id"], {"backdrops": [], "posters": [], "logos": []}
        )
        target[row["image_type"]].append(
            {"image_type": row["image_type"], "image_position": row["image_position"],
             "local_path": row["local_path"]}
        )
    for item in items:
        item["image_options"] = options.get(
            item["internal_media_id"], {"backdrops": [], "posters": [], "logos": []}
        )
    filtered = [
        item for item in items if media_type == "all" or item["media_type"] == media_type
    ]
    if query:
        normalized = query.strip().casefold()
        filtered = [
            item for item in filtered
            if normalized in str(item["media_id"])
            or normalized in str(item.get("name") or "").casefold()
            or normalized in str(item.get("original_name") or "").casefold()
        ]
    filtered.sort(
        key=lambda item: (item.get("source_id") or 0, item["media_id"]), reverse=True
    )
    return {
        "stats": stats, "media_items": filtered, "total_filtered": len(filtered),
        "query": query or "", "media_type": media_type,
    }
