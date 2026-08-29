import json
import sqlite3
from contextlib import closing
from pathlib import Path


MEDIA_RELATIONS = {
    "movie": {
        "owner_column": "movie_id",
        "genre_table": "movie_genre",
        "origin_country_table": "movie_origin_country",
        "production_country_table": "movie_production_country",
        "production_company_table": "movie_production_company",
        "spoken_language_table": "movie_spoken_language",
    },
    "tv": {
        "owner_column": "series_id",
        "genre_table": "tv_series_genre",
        "origin_country_table": "tv_series_origin_country",
        "production_country_table": "tv_series_production_country",
        "production_company_table": "tv_series_production_company",
        "spoken_language_table": "tv_series_spoken_language",
    },
}
MEDIA_IMAGES = {
    "movie": ("movie_image", "movie_id"),
    "tv": ("tv_series_image", "series_id"),
}
MEDIA_IMAGE_TYPES = ("backdrops", "posters", "logos")
MEDIA_SOURCES = {
    "movie": ("movie_source", "movie_id"),
    "tv": ("tv_episode_source", "episode_id"),
}
SOURCE_MEDIA_COLUMNS = (
    "media_duration_ms",
    "file_size_bytes",
    "bit_rate",
    "container_format",
    "video_codec",
    "video_width",
    "video_height",
    "video_frame_rate",
    "video_pixel_format",
    "video_dynamic_range",
    "audio_codec",
    "audio_channels",
    "audio_channel_layout",
)
MEDIA_PROGRESS = {
    "movie": ("movie_watch_progress", "movie_id"),
    "tv": ("tv_episode_watch_progress", "episode_id"),
}
WATCH_RESUME_MIN_MS = 10_000
WATCH_COMPLETE_REMAINING_MS = 120_000
WATCH_COMPLETE_RATIO = 0.95


def _connect(database_path: str | Path):
    connection = sqlite3.connect(database_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def get_media_source(
    database_path: str | Path,
    media_type: str,
    source_id: int,
):
    """Return one playable source and its movie or episode identity."""
    if media_type not in MEDIA_SOURCES:
        raise ValueError("不支持的媒体类型。")

    table_name, owner_column = MEDIA_SOURCES[media_type]
    with closing(_connect(database_path)) as db:
        row = db.execute(
            f"""
            SELECT source_id,
                   {owner_column} AS media_id,
                   alist_path
              FROM {table_name}
             WHERE source_id = ?
            """,
            (source_id,),
        ).fetchone()

    return dict(row) if row else None


def get_media_source_path(
    database_path: str | Path,
    media_type: str,
    source_id: int,
):
    """Return the stored AList path for one playable source."""
    source = get_media_source(database_path, media_type, source_id)
    return source["alist_path"] if source else None


def _is_watch_completed(position_ms: int, duration_ms: int):
    if duration_ms <= 0:
        return False

    remaining_ms = max(duration_ms - position_ms, 0)
    return (
        position_ms / duration_ms >= WATCH_COMPLETE_RATIO
        or remaining_ms <= WATCH_COMPLETE_REMAINING_MS
    )


def _format_time_label(position_ms: int) -> str:
    total_seconds = max(int(position_ms or 0), 0) // 1000
    total_minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _add_progress_fields(item: dict):
    position_ms = max(int(item.get("position_ms") or 0), 0)
    duration_ms = max(int(item.get("duration_ms") or 0), 0)
    if duration_ms:
        position_ms = min(position_ms, duration_ms)
    is_completed = _is_watch_completed(position_ms, duration_ms)

    item["position_ms"] = position_ms
    item["duration_ms"] = duration_ms
    item["position_label"] = _format_time_label(position_ms) if position_ms > 0 else ""
    item["duration_label"] = _format_time_label(duration_ms) if duration_ms > 0 else ""
    item["progress"] = (
        min(round(position_ms * 100 / duration_ms), 100)
        if duration_ms
        else 0
    )
    item["is_completed"] = is_completed
    item["resume_position_ms"] = (
        position_ms
        if position_ms >= WATCH_RESUME_MIN_MS and not is_completed
        else 0
    )

    return item


def get_watch_progress(
    database_path: str | Path,
    media_type: str,
    media_id: int,
):
    """Return saved progress for one movie or episode."""
    if media_type not in MEDIA_PROGRESS:
        raise ValueError("不支持的媒体类型。")

    table_name, owner_column = MEDIA_PROGRESS[media_type]
    with closing(_connect(database_path)) as db:
        row = db.execute(
            f"""
            SELECT position_ms,
                   duration_ms,
                   updated_at
              FROM {table_name}
             WHERE {owner_column} = ?
            """,
            (media_id,),
        ).fetchone()

    return _add_progress_fields(dict(row) if row else {})


def save_watch_progress(
    database_path: str | Path,
    media_type: str,
    media_id: int,
    position_ms: int,
    duration_ms: int,
):
    """Insert or update the last observed playback position."""
    if media_type not in MEDIA_PROGRESS:
        raise ValueError("不支持的媒体类型。")

    table_name, owner_column = MEDIA_PROGRESS[media_type]
    position_ms = max(int(position_ms), 0)
    duration_ms = max(int(duration_ms), 0)
    if duration_ms:
        position_ms = min(position_ms, duration_ms)

    with closing(_connect(database_path)) as db:
        with db:
            db.execute(
                f"""
                INSERT INTO {table_name} (
                            {owner_column},
                            position_ms,
                            duration_ms,
                            updated_at
                       )
                     VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT ({owner_column})
                DO UPDATE SET position_ms = excluded.position_ms,
                              duration_ms = excluded.duration_ms,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (media_id, position_ms, duration_ms),
            )


def _upsert_country(
    db: sqlite3.Connection,
    country_code: str,
    country_name: str | None = None,
):
    db.execute(
        """
        INSERT INTO country (country_code, country_name)
             VALUES (?, ?)
        ON CONFLICT (country_code)
        DO UPDATE SET country_name = COALESCE(excluded.country_name, country_name)
        """,
        (country_code, country_name),
    )


def _assert_source_available(
    db: sqlite3.Connection,
    media_type: str,
    alist_path: str,
):
    other_type = "tv" if media_type == "movie" else "movie"
    table_name, _ = MEDIA_SOURCES[other_type]
    if db.execute(
        f"SELECT 1 FROM {table_name} WHERE alist_path = ?",
        (alist_path,),
    ).fetchone():
        other_label = "剧集单集" if other_type == "tv" else "电影"
        raise ValueError(f"该 AList 文件已关联至{other_label}。")


def _upsert_source(
    db: sqlite3.Connection,
    media_type: str,
    owner_id: int,
    alist_path: str,
    media_metadata: dict | None = None,
):
    table_name, owner_column = MEDIA_SOURCES[media_type]
    db.execute(
        f"""
        INSERT INTO {table_name} ({owner_column}, alist_path)
             VALUES (?, ?)
        ON CONFLICT (alist_path)
        DO UPDATE SET {owner_column} = excluded.{owner_column}
        """,
        (owner_id, alist_path),
    )
    if media_metadata is not None:
        _update_source_media_metadata(
            db,
            media_type,
            alist_path,
            media_metadata,
        )


def _update_source_media_metadata(
    db: sqlite3.Connection,
    media_type: str,
    alist_path: str,
    media_metadata: dict,
):
    table_name, _ = MEDIA_SOURCES[media_type]
    assignments = ",\n                       ".join(
        f"{column} = :{column}" for column in SOURCE_MEDIA_COLUMNS
    )
    values = {
        column: media_metadata.get(column)
        for column in SOURCE_MEDIA_COLUMNS
    }
    values["alist_path"] = alist_path
    db.execute(
        f"""
        UPDATE {table_name}
           SET {assignments},
               probed_at = CURRENT_TIMESTAMP
         WHERE alist_path = :alist_path
        """,
        values,
    )


def save_source_media_metadata(
    database_path: str | Path,
    media_type: str,
    source_id: int,
    media_metadata: dict,
):
    """Persist ffprobe metadata for one existing movie or episode source."""
    if media_type not in MEDIA_SOURCES:
        raise ValueError("不支持的媒体类型。")

    table_name, _ = MEDIA_SOURCES[media_type]
    assignments = ",\n                       ".join(
        f"{column} = :{column}" for column in SOURCE_MEDIA_COLUMNS
    )
    values = {
        column: media_metadata.get(column)
        for column in SOURCE_MEDIA_COLUMNS
    }
    values["source_id"] = source_id
    with closing(_connect(database_path)) as db:
        with db:
            cursor = db.execute(
                f"""
                UPDATE {table_name}
                   SET {assignments},
                       probed_at = CURRENT_TIMESTAMP
                 WHERE source_id = :source_id
                """,
                values,
            )
            if cursor.rowcount == 0:
                raise ValueError("媒体来源不存在。")


def get_sources_missing_media_metadata(database_path: str | Path):
    """Return sources that have not yet been successfully read by ffprobe."""
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT 'movie' AS media_type,
                   source_id,
                   alist_path
              FROM movie_source
             WHERE probed_at IS NULL

             UNION ALL

            SELECT 'tv' AS media_type,
                   source_id,
                   alist_path
              FROM tv_episode_source
             WHERE probed_at IS NULL
             ORDER BY media_type,
                      source_id
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_source_paths(database_path: str | Path):
    """Return every stored AList source path for both media types."""
    with closing(_connect(database_path)) as db:
        paths = set()
        for table in ("movie_source", "tv_episode_source"):
            paths.update(
                row[0]
                for row in db.execute(f"SELECT alist_path FROM {table}")
            )

    return paths


def has_movie(database_path: str | Path, movie_id: int):
    """Return whether the movie is already stored."""
    with closing(_connect(database_path)) as db:
        row = db.execute(
            "SELECT 1 FROM movie WHERE movie_id = ?",
            (movie_id,),
        ).fetchone()

    return row is not None


def find_tv_episode_id(
    database_path: str | Path,
    series_id: int,
    season_number: int,
    episode_number: int,
):
    """Return the stored episode id at a season/episode position, if any."""
    with closing(_connect(database_path)) as db:
        row = db.execute(
            """
            SELECT e.episode_id
              FROM tv_episode AS e
                   INNER JOIN tv_season AS s
                   ON s.season_id = e.season_id
             WHERE s.series_id = ?
               AND s.season_number = ?
               AND e.episode_number = ?
            """,
            (series_id, season_number, episode_number),
        ).fetchone()

    return int(row["episode_id"]) if row else None


def add_movie_source(
    database_path: str | Path,
    movie_id: int,
    alist_path: str,
    media_metadata: dict | None = None,
):
    """Attach an extra AList file to an already stored movie."""
    with closing(_connect(database_path)) as db:
        with db:
            _assert_source_available(db, "movie", alist_path)
            _upsert_source(
                db,
                "movie",
                movie_id,
                alist_path,
                media_metadata,
            )


def add_tv_episode_source(
    database_path: str | Path,
    episode_id: int,
    alist_path: str,
    media_metadata: dict | None = None,
):
    """Attach an extra AList file to an already stored episode."""
    with closing(_connect(database_path)) as db:
        with db:
            _assert_source_available(db, "tv", alist_path)
            _upsert_source(
                db,
                "tv",
                episode_id,
                alist_path,
                media_metadata,
            )


def find_episodes_missing_stills(database_path: str | Path):
    """Return episodes that have sources but no stored still image."""
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT e.episode_id,
                   s.series_id,
                   s.season_number,
                   e.episode_number
              FROM tv_episode AS e
                   INNER JOIN tv_season AS s
                   ON s.season_id = e.season_id
                   INNER JOIN tv_episode_source AS src
                   ON src.episode_id = e.episode_id
                   LEFT JOIN tv_episode_image AS img
                   ON img.episode_id = e.episode_id
             WHERE img.episode_id IS NULL
             GROUP BY e.episode_id
             ORDER BY s.series_id, s.season_number, e.episode_number
            """,
        ).fetchall()

    return [dict(row) for row in rows]


def upsert_tv_episode_still(
    database_path: str | Path,
    episode_id: int,
    local_path: str,
):
    """Store or update the local still path for an episode."""
    with closing(_connect(database_path)) as db:
        with db:
            db.execute(
                """
                INSERT INTO tv_episode_image (episode_id, local_path)
                     VALUES (?, ?)
                ON CONFLICT (episode_id)
                DO UPDATE SET local_path = excluded.local_path
                """,
                (episode_id, local_path),
            )


def _replace_genres(
    db: sqlite3.Connection,
    config: dict,
    owner_id: int,
    genres: list[dict],
):
    owner_column = config["owner_column"]
    relation_table = config["genre_table"]
    db.execute(
        f"DELETE FROM {relation_table} WHERE {owner_column} = ?",
        (owner_id,),
    )
    for position, genre in enumerate(genres, 1):
        db.execute(
            """
            INSERT INTO genre (genre_id, genre_name)
                 VALUES (?, ?)
            ON CONFLICT (genre_id)
            DO UPDATE SET genre_name = excluded.genre_name
            """,
            (genre["id"], genre["name"]),
        )
        db.execute(
            f"""
            INSERT INTO {relation_table} ({owner_column}, genre_id, genre_position)
                 VALUES (?, ?, ?)
            """,
            (owner_id, genre["id"], position),
        )


def _prepare_countries(db: sqlite3.Connection, details: dict):
    production_countries = {
        country["iso_3166_1"]: country.get("name")
        for country in details.get("production_countries", [])
    }
    origin_countries = details.get("origin_country", [])
    companies = details.get("production_companies", [])
    country_codes = set(origin_countries) | set(production_countries)
    country_codes.update(
        company.get("origin_country")
        for company in companies
        if company.get("origin_country") is not None
    )
    for country_code in country_codes:
        _upsert_country(db, country_code, production_countries.get(country_code))

    return origin_countries, production_countries, companies


def _replace_countries(
    db: sqlite3.Connection,
    config: dict,
    owner_id: int,
    details: dict,
):
    owner_column = config["owner_column"]
    origin_table = config["origin_country_table"]
    production_table = config["production_country_table"]
    origin_countries, _, companies = _prepare_countries(db, details)

    for table_name in (origin_table, production_table):
        db.execute(
            f"DELETE FROM {table_name} WHERE {owner_column} = ?",
            (owner_id,),
        )
    for position, country_code in enumerate(origin_countries, 1):
        db.execute(
            f"""
            INSERT INTO {origin_table} (
                {owner_column}, country_code, country_position
            )
                 VALUES (?, ?, ?)
            """,
            (owner_id, country_code, position),
        )
    for position, country in enumerate(details.get("production_countries", []), 1):
        db.execute(
            f"""
            INSERT INTO {production_table} (
                {owner_column}, country_code, country_position
            )
                 VALUES (?, ?, ?)
            """,
            (owner_id, country["iso_3166_1"], position),
        )

    return companies


def _replace_companies(
    db: sqlite3.Connection,
    config: dict,
    owner_id: int,
    companies: list[dict],
):
    owner_column = config["owner_column"]
    relation_table = config["production_company_table"]
    db.execute(
        f"DELETE FROM {relation_table} WHERE {owner_column} = ?",
        (owner_id,),
    )
    for position, company in enumerate(companies, 1):
        db.execute(
            """
            INSERT INTO production_company (
                company_id, company_name, origin_country_code, logo_path
            )
                 VALUES (?, ?, ?, ?)
            ON CONFLICT (company_id)
            DO UPDATE SET company_name = excluded.company_name,
                          origin_country_code = excluded.origin_country_code,
                          logo_path = excluded.logo_path
            """,
            (
                company["id"],
                company["name"],
                company.get("origin_country"),
                company.get("logo_path"),
            ),
        )
        db.execute(
            f"""
            INSERT INTO {relation_table} (
                {owner_column}, company_id, company_position
            )
                 VALUES (?, ?, ?)
            """,
            (owner_id, company["id"], position),
        )


def _replace_languages(
    db: sqlite3.Connection,
    config: dict,
    owner_id: int,
    languages: list[dict],
):
    owner_column = config["owner_column"]
    relation_table = config["spoken_language_table"]
    db.execute(
        f"DELETE FROM {relation_table} WHERE {owner_column} = ?",
        (owner_id,),
    )
    for position, language in enumerate(languages, 1):
        language_code = language.get("iso_639_1")
        if not language_code:
            continue
        db.execute(
            """
            INSERT INTO spoken_language (language_code, english_name, native_name)
                 VALUES (?, ?, ?)
            ON CONFLICT (language_code)
            DO UPDATE SET english_name = excluded.english_name,
                          native_name = excluded.native_name
            """,
            (
                language_code,
                language.get("english_name") or "",
                language.get("name") or "",
            ),
        )
        db.execute(
            f"""
            INSERT INTO {relation_table} (
                {owner_column}, language_code, language_position
            )
                 VALUES (?, ?, ?)
            """,
            (owner_id, language_code, position),
        )


def _replace_shared_relations(
    db: sqlite3.Connection,
    media_type: str,
    owner_id: int,
    details: dict,
):
    config = MEDIA_RELATIONS[media_type]
    _replace_genres(db, config, owner_id, details.get("genres", []))
    companies = _replace_countries(db, config, owner_id, details)
    _replace_companies(db, config, owner_id, companies)
    _replace_languages(
        db,
        config,
        owner_id,
        details.get("spoken_languages", []),
    )


def _replace_media_images(
    db: sqlite3.Connection,
    media_type: str,
    owner_id: int,
    images: list[dict],
):
    table_name, owner_column = MEDIA_IMAGES[media_type]
    preferred_paths = {
        row["image_type"]: row["local_path"]
        for row in db.execute(
            f"""
            SELECT image_type,
                   local_path
              FROM {table_name}
             WHERE {owner_column} = ?
               AND image_position = 1
            """,
            (owner_id,),
        )
    }
    db.execute(
        f"DELETE FROM {table_name} WHERE {owner_column} = ?",
        (owner_id,),
    )
    for image_type in MEDIA_IMAGE_TYPES:
        type_images = sorted(
            (
                image
                for image in images
                if image["image_type"] == image_type
            ),
            key=lambda image: (
                image["local_path"] != preferred_paths.get(image_type),
                image["image_position"],
            ),
        )
        for image_position, image in enumerate(type_images, start=1):
            db.execute(
                f"""
                INSERT INTO {table_name} (
                    {owner_column},
                    image_type,
                    image_position,
                    image_url,
                    local_path
                )
                     VALUES (?, ?, ?, ?, ?)
                """,
                (
                    owner_id,
                    image_type,
                    image_position,
                    image["image_url"],
                    image["local_path"],
                ),
            )


def save_movie(
    database_path: str | Path,
    details: dict,
    images: list[dict],
    alist_path: str,
    source_metadata: dict | None = None,
):
    """Persist TMDB details, local images and an AList mapping atomically."""
    with closing(_connect(database_path)) as db:
        with db:
            _assert_source_available(db, "movie", alist_path)
            _save_movie(
                db,
                details,
                images,
                alist_path,
                source_metadata,
            )


def _save_movie(
    db: sqlite3.Connection,
    details: dict,
    images: list[dict],
    alist_path: str,
    source_metadata: dict | None,
):
    movie_id = int(details["id"])
    collection = details.get("belongs_to_collection")
    collection_id = collection.get("id") if collection else None

    if collection:
        db.execute(
            """
            INSERT INTO collection (
                collection_id,
                collection_name,
                poster_path,
                backdrop_path
            )
                 VALUES (?, ?, ?, ?)
            ON CONFLICT (collection_id)
            DO UPDATE SET collection_name = excluded.collection_name,
                          poster_path = excluded.poster_path,
                          backdrop_path = excluded.backdrop_path
            """,
            (
                collection["id"],
                collection["name"],
                collection.get("poster_path"),
                collection.get("backdrop_path"),
            ),
        )

    movie_values = {
        "movie_id": movie_id,
        "collection_id": collection_id,
        "adult": int(bool(details.get("adult"))),
        "softcore": int(bool(details.get("softcore"))),
        "video": int(bool(details.get("video"))),
        "budget": details.get("budget") or 0,
        "revenue": details.get("revenue") or 0,
        "runtime": details.get("runtime"),
        "popularity": details.get("popularity") or 0,
        "vote_average": details.get("vote_average") or 0,
        "vote_count": details.get("vote_count") or 0,
        "original_language": details.get("original_language") or "",
        "original_title": details.get("original_title") or "",
        "title": details.get("title") or "",
        "overview": details.get("overview"),
        "tagline": details.get("tagline"),
        "homepage": details.get("homepage"),
        "imdb_id": details.get("imdb_id"),
        "release_date": details.get("release_date"),
        "release_status": details.get("status") or "",
    }
    db.execute(
        """
        INSERT INTO movie (
            movie_id,
            collection_id,
            adult,
            softcore,
            video,
            budget,
            revenue,
            runtime,
            popularity,
            vote_average,
            vote_count,
            original_language,
            original_title,
            title,
            overview,
            tagline,
            homepage,
            imdb_id,
            release_date,
            release_status
        )
             VALUES (
                :movie_id,
                :collection_id,
                :adult,
                :softcore,
                :video,
                :budget,
                :revenue,
                :runtime,
                :popularity,
                :vote_average,
                :vote_count,
                :original_language,
                :original_title,
                :title,
                :overview,
                :tagline,
                :homepage,
                :imdb_id,
                :release_date,
                :release_status
            )
        ON CONFLICT (movie_id)
        DO UPDATE SET collection_id = excluded.collection_id,
                      adult = excluded.adult,
                      softcore = excluded.softcore,
                      video = excluded.video,
                      budget = excluded.budget,
                      revenue = excluded.revenue,
                      runtime = excluded.runtime,
                      popularity = excluded.popularity,
                      vote_average = excluded.vote_average,
                      vote_count = excluded.vote_count,
                      original_language = excluded.original_language,
                      original_title = excluded.original_title,
                      title = excluded.title,
                      overview = excluded.overview,
                      tagline = excluded.tagline,
                      homepage = excluded.homepage,
                      imdb_id = excluded.imdb_id,
                      release_date = excluded.release_date,
                      release_status = excluded.release_status
        """,
        movie_values,
    )

    _replace_shared_relations(db, "movie", movie_id, details)
    _replace_media_images(db, "movie", movie_id, images)
    _upsert_source(
        db,
        "movie",
        movie_id,
        alist_path,
        source_metadata,
    )


def _json_text(value):
    return json.dumps(value or [], ensure_ascii=False, separators=(",", ":"))


def save_tv_episode(
    database_path: str | Path,
    series_details: dict,
    season_details: dict,
    episode_number: int,
    alist_path: str,
    series_images: list[dict] | None = None,
    episode_still: dict | None = None,
    source_metadata: dict | None = None,
):
    """Persist a series, selected season, all its episodes, and one source."""
    with closing(_connect(database_path)) as db:
        with db:
            _assert_source_available(db, "tv", alist_path)
            _save_tv_episode(
                db,
                series_details,
                season_details,
                episode_number,
                alist_path,
                series_images,
                episode_still,
                source_metadata,
            )


def _save_tv_episode(
    db: sqlite3.Connection,
    series_details: dict,
    season_details: dict,
    episode_number: int,
    alist_path: str,
    series_images: list[dict] | None,
    episode_still: dict | None,
    source_metadata: dict | None,
):
    series_id = int(series_details["id"])
    series_values = {
        "series_id": series_id,
        "adult": int(bool(series_details.get("adult"))),
        "softcore": int(bool(series_details.get("softcore"))),
        "in_production": int(bool(series_details.get("in_production"))),
        "number_of_episodes": series_details.get("number_of_episodes") or 0,
        "number_of_seasons": series_details.get("number_of_seasons") or 0,
        "popularity": series_details.get("popularity") or 0,
        "vote_average": series_details.get("vote_average") or 0,
        "vote_count": series_details.get("vote_count") or 0,
        "original_language": series_details.get("original_language") or "",
        "original_name": series_details.get("original_name") or "",
        "series_name": series_details.get("name") or "",
        "overview": series_details.get("overview"),
        "tagline": series_details.get("tagline"),
        "homepage": series_details.get("homepage"),
        "imdb_id": series_details.get("imdb_id"),
        "first_air_date": series_details.get("first_air_date"),
        "last_air_date": series_details.get("last_air_date"),
        "series_status": series_details.get("status") or "",
        "series_type": series_details.get("type") or "",
        "poster_path": series_details.get("poster_path"),
        "backdrop_path": series_details.get("backdrop_path"),
        "created_by_json": _json_text(series_details.get("created_by")),
        "networks_json": _json_text(series_details.get("networks")),
        "episode_runtime_json": _json_text(
            series_details.get("episode_run_time")
        ),
    }
    db.execute(
        """
        INSERT INTO tv_series (
            series_id,
            adult,
            softcore,
            in_production,
            number_of_episodes,
            number_of_seasons,
            popularity,
            vote_average,
            vote_count,
            original_language,
            original_name,
            series_name,
            overview,
            tagline,
            homepage,
            imdb_id,
            first_air_date,
            last_air_date,
            series_status,
            series_type,
            poster_path,
            backdrop_path,
            created_by_json,
            networks_json,
            episode_runtime_json,
            last_episode_id,
            next_episode_id
        )
             VALUES (
                :series_id,
                :adult,
                :softcore,
                :in_production,
                :number_of_episodes,
                :number_of_seasons,
                :popularity,
                :vote_average,
                :vote_count,
                :original_language,
                :original_name,
                :series_name,
                :overview,
                :tagline,
                :homepage,
                :imdb_id,
                :first_air_date,
                :last_air_date,
                :series_status,
                :series_type,
                :poster_path,
                :backdrop_path,
                :created_by_json,
                :networks_json,
                :episode_runtime_json,
                NULL,
                NULL
            )
        ON CONFLICT (series_id)
        DO UPDATE SET adult = excluded.adult,
                      softcore = excluded.softcore,
                      in_production = excluded.in_production,
                      number_of_episodes = excluded.number_of_episodes,
                      number_of_seasons = excluded.number_of_seasons,
                      popularity = excluded.popularity,
                      vote_average = excluded.vote_average,
                      vote_count = excluded.vote_count,
                      original_language = excluded.original_language,
                      original_name = excluded.original_name,
                      series_name = excluded.series_name,
                      overview = excluded.overview,
                      tagline = excluded.tagline,
                      homepage = excluded.homepage,
                      imdb_id = COALESCE(excluded.imdb_id, tv_series.imdb_id),
                      first_air_date = excluded.first_air_date,
                      last_air_date = excluded.last_air_date,
                      series_status = excluded.series_status,
                      series_type = excluded.series_type,
                      poster_path = excluded.poster_path,
                      backdrop_path = excluded.backdrop_path,
                      created_by_json = excluded.created_by_json,
                      networks_json = excluded.networks_json,
                      episode_runtime_json = excluded.episode_runtime_json,
                      last_episode_id = NULL,
                      next_episode_id = NULL
        """,
        series_values,
    )

    _replace_shared_relations(db, "tv", series_id, series_details)
    selected_episode_id, season_by_number = _save_tv_season_catalog(
        db,
        series_id,
        series_details.get("seasons", []),
        season_details,
        episode_number,
    )
    _update_tv_episode_pointers(
        db,
        series_id,
        series_details,
        season_by_number,
    )
    _upsert_source(
        db,
        "tv",
        selected_episode_id,
        alist_path,
        source_metadata,
    )

    if series_images is not None:
        _replace_media_images(db, "tv", series_id, series_images)

    if episode_still is not None:
        _upsert_tv_episode_still(db, selected_episode_id, episode_still)


def _upsert_tv_season(db: sqlite3.Connection, series_id: int, season: dict):
    season_id = int(season["id"])
    has_full_details = int("episodes" in season or "_id" in season)
    values = {
        "season_id": season_id,
        "series_id": series_id,
        "season_number": int(season.get("season_number", 0)),
        "episode_count": season.get("episode_count")
        if season.get("episode_count") is not None
        else len(season.get("episodes", [])),
        "season_name": season.get("name") or "",
        "overview": season.get("overview"),
        "air_date": season.get("air_date"),
        "poster_path": season.get("poster_path"),
        "vote_average": season.get("vote_average") or 0,
        "tmdb_internal_id": season.get("_id"),
        "networks_json": _json_text(season.get("networks")),
        "has_full_details": has_full_details,
    }
    db.execute(
        """
        INSERT INTO tv_season (
            season_id,
            series_id,
            season_number,
            episode_count,
            season_name,
            overview,
            air_date,
            poster_path,
            vote_average,
            tmdb_internal_id,
            networks_json
        )
             VALUES (
                :season_id,
                :series_id,
                :season_number,
                :episode_count,
                :season_name,
                :overview,
                :air_date,
                :poster_path,
                :vote_average,
                :tmdb_internal_id,
                :networks_json
            )
        ON CONFLICT (season_id)
        DO UPDATE SET series_id = excluded.series_id,
                      season_number = excluded.season_number,
                      episode_count = excluded.episode_count,
                      season_name = excluded.season_name,
                      overview = excluded.overview,
                      air_date = excluded.air_date,
                      poster_path = excluded.poster_path,
                      vote_average = excluded.vote_average,
                      tmdb_internal_id = COALESCE(
                          excluded.tmdb_internal_id,
                          tv_season.tmdb_internal_id
                      ),
                      networks_json = CASE :has_full_details
                          WHEN 1 THEN excluded.networks_json
                          ELSE tv_season.networks_json
                      END
        """,
        values,
    )

    return season_id


def _upsert_tv_episode(
    db: sqlite3.Connection,
    season_id: int,
    episode: dict,
    update_nested: bool,
):
    values = {
        "episode_id": int(episode["id"]),
        "season_id": season_id,
        "episode_number": int(episode["episode_number"]),
        "episode_name": episode.get("name") or "",
        "overview": episode.get("overview"),
        "air_date": episode.get("air_date"),
        "runtime": episode.get("runtime"),
        "production_code": episode.get("production_code"),
        "episode_type": episode.get("episode_type"),
        "still_path": episode.get("still_path"),
        "vote_average": episode.get("vote_average") or 0,
        "vote_count": episode.get("vote_count") or 0,
        "crew_json": _json_text(episode.get("crew")),
        "guest_stars_json": _json_text(episode.get("guest_stars")),
        "update_nested": int(update_nested),
    }
    db.execute(
        """
        INSERT INTO tv_episode (
            episode_id,
            season_id,
            episode_number,
            episode_name,
            overview,
            air_date,
            runtime,
            production_code,
            episode_type,
            still_path,
            vote_average,
            vote_count,
            crew_json,
            guest_stars_json
        )
             VALUES (
                :episode_id,
                :season_id,
                :episode_number,
                :episode_name,
                :overview,
                :air_date,
                :runtime,
                :production_code,
                :episode_type,
                :still_path,
                :vote_average,
                :vote_count,
                :crew_json,
                :guest_stars_json
            )
        ON CONFLICT (episode_id)
        DO UPDATE SET season_id = excluded.season_id,
                      episode_number = excluded.episode_number,
                      episode_name = excluded.episode_name,
                      overview = excluded.overview,
                      air_date = excluded.air_date,
                      runtime = excluded.runtime,
                      production_code = excluded.production_code,
                      episode_type = excluded.episode_type,
                      still_path = excluded.still_path,
                      vote_average = excluded.vote_average,
                      vote_count = excluded.vote_count,
                      crew_json = CASE :update_nested
                          WHEN 1 THEN excluded.crew_json
                          ELSE tv_episode.crew_json
                      END,
                      guest_stars_json = CASE :update_nested
                          WHEN 1 THEN excluded.guest_stars_json
                          ELSE tv_episode.guest_stars_json
                      END
        """,
        values,
    )

    return values["episode_id"]


def _save_tv_season_catalog(
    db: sqlite3.Connection,
    series_id: int,
    seasons: list[dict],
    selected_season: dict,
    selected_episode_number: int,
):
    season_by_number = {}
    for season in seasons:
        season_id = _upsert_tv_season(db, series_id, season)
        season_by_number[int(season.get("season_number", 0))] = season_id

    selected_season_id = _upsert_tv_season(db, series_id, selected_season)
    selected_season_number = int(selected_season["season_number"])
    season_by_number[selected_season_number] = selected_season_id

    selected_episode_id = None
    for episode in selected_season.get("episodes", []):
        episode_id = _upsert_tv_episode(db, selected_season_id, episode, True)
        if int(episode.get("episode_number", -1)) == selected_episode_number:
            selected_episode_id = episode_id

    if selected_episode_id is None:
        raise ValueError("TMDB 中未找到指定季和集数。")

    return selected_episode_id, season_by_number


def _update_tv_episode_pointers(
    db: sqlite3.Connection,
    series_id: int,
    details: dict,
    season_by_number: dict[int, int],
):
    pointer_ids = []
    for detail_key in ("last_episode_to_air", "next_episode_to_air"):
        episode = details.get(detail_key)
        season_id = (
            season_by_number.get(int(episode.get("season_number", -1)))
            if episode
            else None
        )
        pointer_ids.append(
            _upsert_tv_episode(db, season_id, episode, False)
            if season_id is not None
            else None
        )

    db.execute(
        """
        UPDATE tv_series
           SET last_episode_id = ?,
               next_episode_id = ?
         WHERE series_id = ?
        """,
        (*pointer_ids, series_id),
    )


def _upsert_tv_episode_still(
    db: sqlite3.Connection,
    selected_episode_id: int,
    episode_still: dict,
):
    if int(episode_still["episode_id"]) != selected_episode_id:
        raise ValueError("单集剧照与所选 Episode 不一致。")
    db.execute(
        """
        INSERT INTO tv_episode_image (episode_id, local_path)
             VALUES (?, ?)
        ON CONFLICT (episode_id)
        DO UPDATE SET local_path = excluded.local_path
        """,
        (selected_episode_id, episode_still["local_path"]),
    )


def get_movie_mappings(database_path: str | Path):
    """Return every stored AList-to-movie relationship."""
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT ms.source_id,
                   ms.alist_path,
                   m.movie_id,
                   m.title,
                   m.original_title,
                   m.release_date
              FROM movie_source AS ms
                   INNER JOIN movie AS m
                   ON m.movie_id = ms.movie_id
             ORDER BY m.title, ms.source_id
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_tv_episode_mappings(database_path: str | Path):
    """Return every stored AList-to-TV-episode relationship."""
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT tes.source_id,
                   tes.alist_path,
                   ts.series_id,
                   ts.series_name,
                   ts.original_name,
                   season.season_number,
                   episode.episode_id,
                   episode.episode_number,
                   episode.episode_name,
                   episode.air_date
              FROM tv_episode_source AS tes
                   INNER JOIN tv_episode AS episode
                   ON episode.episode_id = tes.episode_id
                   INNER JOIN tv_season AS season
                   ON season.season_id = episode.season_id
                   INNER JOIN tv_series AS ts
                   ON ts.series_id = season.series_id
             ORDER BY ts.series_name,
                      season.season_number,
                      episode.episode_number,
                      tes.source_id
            """
        ).fetchall()

    return [dict(row) for row in rows]


def get_tv_series_images(database_path: str | Path, series_id: int):
    """Return persisted local image records for one TV series."""
    with closing(_connect(database_path)) as db:
        rows = db.execute(
            """
            SELECT image_type,
                   image_position,
                   image_url,
                   local_path
              FROM tv_series_image
             WHERE series_id = ?
             ORDER BY image_type, image_position
            """,
            (series_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def set_primary_media_image(
    database_path: str | Path,
    media_type: str,
    media_id: int,
    image_type: str,
    image_position: int,
):
    """Make one stored image the primary image for its media and type."""
    if media_type not in MEDIA_IMAGES:
        raise ValueError("不支持的媒体类型。")
    if image_type not in MEDIA_IMAGE_TYPES:
        raise ValueError("不支持的图片类型。")

    table_name, owner_column = MEDIA_IMAGES[media_type]
    with closing(_connect(database_path)) as db:
        with db:
            rows = db.execute(
                f"""
                SELECT image_position,
                       image_url,
                       local_path
                  FROM {table_name}
                 WHERE {owner_column} = ?
                   AND image_type = ?
                   AND image_position IN (1, ?)
                 ORDER BY image_position
                """,
                (media_id, image_type, image_position),
            ).fetchall()
            images = {
                row["image_position"]: dict(row)
                for row in rows
            }
            selected = images.get(image_position)
            if selected is None:
                raise ValueError("指定图片不存在。")
            if image_position == 1:
                return

            primary = images.get(1)
            db.execute(
                f"""
                DELETE FROM {table_name}
                 WHERE {owner_column} = ?
                   AND image_type = ?
                   AND image_position IN (1, ?)
                """,
                (media_id, image_type, image_position),
            )
            db.execute(
                f"""
                INSERT INTO {table_name} (
                    {owner_column},
                    image_type,
                    image_position,
                    image_url,
                    local_path
                )
                     VALUES (?, ?, 1, ?, ?)
                """,
                (
                    media_id,
                    image_type,
                    selected["image_url"],
                    selected["local_path"],
                ),
            )
            if primary:
                db.execute(
                    f"""
                    INSERT INTO {table_name} (
                        {owner_column},
                        image_type,
                        image_position,
                        image_url,
                        local_path
                    )
                         VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        media_id,
                        image_type,
                        image_position,
                        primary["image_url"],
                        primary["local_path"],
                    ),
                )


def _get_homepage_movie_items(db: sqlite3.Connection):
    rows = db.execute(
        """
        SELECT 'movie' AS media_type,
               movie.movie_id AS media_id,
               movie.title AS name,
               movie.original_title AS original_name,
               SUBSTR(movie.release_date, 1, 4) AS year,
               movie.release_date AS release_date,
               movie.vote_average AS vote_average,
               movie.overview AS overview,
               source.source_id AS source_id,
               source.alist_path AS alist_path,
               (
                   SELECT image.local_path
                     FROM movie_image AS image
                    WHERE image.movie_id = movie.movie_id
                      AND image.image_type = 'posters'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS poster_path,
               (
                   SELECT image.local_path
                     FROM movie_image AS image
                    WHERE image.movie_id = movie.movie_id
                      AND image.image_type = 'backdrops'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS backdrop_path,
               (
                   SELECT image.local_path
                     FROM movie_image AS image
                    WHERE image.movie_id = movie.movie_id
                      AND image.image_type = 'logos'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS logo_path
          FROM movie
               INNER JOIN movie_source AS source
               ON source.source_id = (
                   SELECT candidate.source_id
                     FROM movie_source AS candidate
                    WHERE candidate.movie_id = movie.movie_id
                    ORDER BY candidate.source_id DESC
                    LIMIT 1
               )
         ORDER BY source.source_id DESC
        """
    ).fetchall()

    return [dict(row) for row in rows]


def _get_homepage_tv_items(db: sqlite3.Connection):
    rows = db.execute(
        """
        WITH linked_episode AS (
            SELECT series.series_id,
                   source.source_id,
                   source.alist_path,
                   season.season_number,
                   episode.episode_number,
                   episode.episode_name,
                   ROW_NUMBER() OVER (
                       PARTITION BY series.series_id
                           ORDER BY source.source_id DESC
                   ) AS source_rank
              FROM tv_episode_source AS source
                   INNER JOIN tv_episode AS episode
                   ON episode.episode_id = source.episode_id
                   INNER JOIN tv_season AS season
                   ON season.season_id = episode.season_id
                   INNER JOIN tv_series AS series
                   ON series.series_id = season.series_id
        )
        SELECT 'tv' AS media_type,
               series.series_id AS media_id,
               series.series_name AS name,
               series.original_name AS original_name,
               SUBSTR(series.first_air_date, 1, 4) AS year,
               series.first_air_date AS release_date,
               series.vote_average AS vote_average,
               series.overview AS overview,
               linked.source_id AS source_id,
               linked.alist_path AS alist_path,
               linked.season_number AS season_number,
               linked.episode_number AS episode_number,
               linked.episode_name AS episode_name,
               (
                   SELECT image.local_path
                     FROM tv_series_image AS image
                    WHERE image.series_id = series.series_id
                      AND image.image_type = 'posters'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS poster_path,
               (
                   SELECT image.local_path
                     FROM tv_series_image AS image
                    WHERE image.series_id = series.series_id
                      AND image.image_type = 'backdrops'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS backdrop_path,
               (
                   SELECT image.local_path
                     FROM tv_series_image AS image
                    WHERE image.series_id = series.series_id
                      AND image.image_type = 'logos'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS logo_path
          FROM tv_series AS series
               INNER JOIN linked_episode AS linked
               ON linked.series_id = series.series_id
              AND linked.source_rank = 1
         ORDER BY linked.source_id DESC
        """
    ).fetchall()

    return [dict(row) for row in rows]


def _get_media_genre_features(
    db: sqlite3.Connection,
    media_type: str,
):
    if media_type == "movie":
        relation_table = "movie_genre"
        media_table = "movie"
        media_id_column = "movie_id"
        media_name_column = "title"
        image_table = "movie_image"
        image_owner_column = "movie_id"
    elif media_type == "tv":
        relation_table = "tv_series_genre"
        media_table = "tv_series"
        media_id_column = "series_id"
        media_name_column = "series_name"
        image_table = "tv_series_image"
        image_owner_column = "series_id"
    else:
        raise ValueError("媒体类型无效。")

    rows = db.execute(
        f"""
        WITH ranked_genre AS (
            SELECT genre.genre_id,
                   genre.genre_name,
                   media.{media_id_column} AS media_id,
                   media.{media_name_column} AS media_name,
                   ROW_NUMBER() OVER (
                       PARTITION BY genre.genre_id
                           ORDER BY media.vote_average DESC,
                                    media.{media_id_column} DESC
                   ) AS media_rank
              FROM {relation_table} AS relation
                   INNER JOIN genre
                   ON genre.genre_id = relation.genre_id
                   INNER JOIN {media_table} AS media
                   ON media.{media_id_column} = relation.{media_id_column}
        )
        SELECT ranked.genre_id,
               ranked.genre_name,
               ranked.media_id,
               ranked.media_name,
               (
                   SELECT image.local_path
                     FROM {image_table} AS image
                    WHERE image.{image_owner_column} = ranked.media_id
                      AND image.image_type = 'backdrops'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS backdrop_path
          FROM ranked_genre AS ranked
         WHERE ranked.media_rank = 1
         ORDER BY ranked.genre_name
        """
    ).fetchall()

    return [dict(row) for row in rows]


def _get_movie_genre_features(db: sqlite3.Connection):
    return _get_media_genre_features(db, "movie")


def _get_tv_genre_features(db: sqlite3.Connection):
    return _get_media_genre_features(db, "tv")


def _filter_library_items(
    db: sqlite3.Connection,
    media_type: str,
    items: list[dict],
    genre_id: int | None,
    country_code: str | None,
    language_code: str | None,
    year: str | None,
    min_rating: float | None,
):
    relation = MEDIA_RELATIONS[media_type]
    owner_column = relation["owner_column"]
    allowed_ids = {item["media_id"] for item in items}
    relation_filters = (
        (relation["genre_table"], "genre_id", genre_id),
        (
            relation["production_country_table"],
            "country_code",
            country_code,
        ),
        (
            relation["spoken_language_table"],
            "language_code",
            language_code,
        ),
    )
    for table_name, filter_column, filter_value in relation_filters:
        if filter_value is None:
            continue
        rows = db.execute(
            f"""
            SELECT {owner_column} AS media_id
              FROM {table_name}
             WHERE {filter_column} = ?
            """,
            (filter_value,),
        ).fetchall()
        allowed_ids &= {row["media_id"] for row in rows}

    return [
        item
        for item in items
        if item["media_id"] in allowed_ids
        and (year is None or item["year"] == year)
        and (min_rating is None or item["vote_average"] >= min_rating)
    ]


def get_filtered_library_items(
    database_path: str | Path,
    media_type: str = "all",
    genre_id: int | None = None,
    country_code: str | None = None,
    language_code: str | None = None,
    year: str | None = None,
    min_rating: float | None = None,
):
    """Return linked movies and series matching the selected filters."""
    if media_type not in ("all", "movie", "tv"):
        raise ValueError("媒体类型无效。")

    selected_types = ("movie", "tv") if media_type == "all" else (media_type,)
    with closing(_connect(database_path)) as db:
        items = []
        for selected_type in selected_types:
            media_items = (
                _get_homepage_movie_items(db)
                if selected_type == "movie"
                else _get_homepage_tv_items(db)
            )
            items.extend(
                _filter_library_items(
                    db,
                    selected_type,
                    media_items,
                    genre_id,
                    country_code,
                    language_code,
                    year,
                    min_rating,
                )
            )

    return sorted(
        items,
        key=lambda item: (item["source_id"], item["media_id"]),
        reverse=True,
    )


def get_library_filter_options(
    database_path: str | Path,
    media_type: str = "all",
):
    """Return filter choices available for movies, series, or both."""
    if media_type not in ("all", "movie", "tv"):
        raise ValueError("媒体类型无效。")

    selected_types = ("movie", "tv") if media_type == "all" else (media_type,)
    genres = {}
    countries = {}
    languages = {}
    years = set()
    with closing(_connect(database_path)) as db:
        for selected_type in selected_types:
            relation = MEDIA_RELATIONS[selected_type]
            for row in db.execute(
                f"""
                SELECT DISTINCT genre.genre_id,
                                genre.genre_name
                  FROM {relation['genre_table']} AS relation
                       INNER JOIN genre
                       ON genre.genre_id = relation.genre_id
                """
            ).fetchall():
                genres[row["genre_id"]] = dict(row)

            for row in db.execute(
                f"""
                SELECT DISTINCT country.country_code,
                                country.country_name
                  FROM {relation['production_country_table']} AS relation
                       INNER JOIN country
                       ON country.country_code = relation.country_code
                 WHERE country.country_code <> ''
                """
            ).fetchall():
                countries[row["country_code"]] = {
                    "country_code": row["country_code"],
                    "country_name": row["country_name"] or row["country_code"],
                }

            for row in db.execute(
                f"""
                SELECT DISTINCT language.language_code,
                                language.english_name,
                                language.native_name
                  FROM {relation['spoken_language_table']} AS relation
                       INNER JOIN spoken_language AS language
                       ON language.language_code = relation.language_code
                """
            ).fetchall():
                languages[row["language_code"]] = {
                    "language_code": row["language_code"],
                    "language_name": (
                        row["native_name"]
                        or row["english_name"]
                        or row["language_code"]
                    ),
                }

            media_items = (
                _get_homepage_movie_items(db)
                if selected_type == "movie"
                else _get_homepage_tv_items(db)
            )
            years.update(item["year"] for item in media_items if item["year"])

    return {
        "genres": sorted(
            genres.values(),
            key=lambda item: item["genre_name"].casefold(),
        ),
        "countries": sorted(
            countries.values(),
            key=lambda item: item["country_name"].casefold(),
        ),
        "languages": sorted(
            languages.values(),
            key=lambda item: item["language_name"].casefold(),
        ),
        "years": sorted(years, reverse=True),
    }


def _get_continue_watching_items(
    db: sqlite3.Connection,
    item_limit: int,
):
    movie_rows = db.execute(
        """
        SELECT 'movie' AS media_type,
               movie.movie_id AS media_id,
               movie.title AS name,
               movie.original_title AS original_name,
               SUBSTR(movie.release_date, 1, 4) AS year,
               movie.release_date AS release_date,
               movie.vote_average AS vote_average,
               movie.overview AS overview,
               source.source_id AS source_id,
               NULL AS season_number,
               NULL AS episode_number,
               NULL AS episode_name,
               progress.position_ms,
               progress.duration_ms,
               progress.updated_at,
               (
                   SELECT image.local_path
                     FROM movie_image AS image
                    WHERE image.movie_id = movie.movie_id
                      AND image.image_type = 'posters'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS poster_path,
               (
                   SELECT image.local_path
                     FROM movie_image AS image
                    WHERE image.movie_id = movie.movie_id
                      AND image.image_type = 'backdrops'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS backdrop_path,
               (
                   SELECT image.local_path
                     FROM movie_image AS image
                    WHERE image.movie_id = movie.movie_id
                      AND image.image_type = 'logos'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS logo_path
          FROM movie_watch_progress AS progress
               INNER JOIN movie
               ON movie.movie_id = progress.movie_id
               INNER JOIN movie_source AS source
               ON source.source_id = (
                   SELECT candidate.source_id
                     FROM movie_source AS candidate
                    WHERE candidate.movie_id = movie.movie_id
                    ORDER BY candidate.source_id DESC
                    LIMIT 1
               )
         WHERE progress.position_ms >= ?
           AND progress.duration_ms > 0
        """,
        (WATCH_RESUME_MIN_MS,),
    ).fetchall()
    tv_rows = db.execute(
        """
        SELECT 'tv' AS media_type,
               series.series_id AS media_id,
               series.series_name AS name,
               series.original_name AS original_name,
               SUBSTR(series.first_air_date, 1, 4) AS year,
               series.first_air_date AS release_date,
               series.vote_average AS vote_average,
               series.overview AS overview,
               source.source_id AS source_id,
               season.season_number,
               episode.episode_number,
               episode.episode_name,
               progress.position_ms,
               progress.duration_ms,
               progress.updated_at,
               (
                   SELECT image.local_path
                     FROM tv_series_image AS image
                    WHERE image.series_id = series.series_id
                      AND image.image_type = 'posters'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS poster_path,
               (
                   SELECT image.local_path
                     FROM tv_series_image AS image
                    WHERE image.series_id = series.series_id
                      AND image.image_type = 'backdrops'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS backdrop_path,
               (
                   SELECT image.local_path
                     FROM tv_series_image AS image
                    WHERE image.series_id = series.series_id
                      AND image.image_type = 'logos'
                    ORDER BY image.image_position
                    LIMIT 1
               ) AS logo_path
          FROM tv_episode_watch_progress AS progress
               INNER JOIN tv_episode AS episode
               ON episode.episode_id = progress.episode_id
               INNER JOIN tv_season AS season
               ON season.season_id = episode.season_id
               INNER JOIN tv_series AS series
               ON series.series_id = season.series_id
               INNER JOIN tv_episode_source AS source
               ON source.source_id = (
                   SELECT candidate.source_id
                     FROM tv_episode_source AS candidate
                    WHERE candidate.episode_id = episode.episode_id
                    ORDER BY candidate.source_id DESC
                    LIMIT 1
               )
         WHERE progress.position_ms >= ?
           AND progress.duration_ms > 0
        """,
        (WATCH_RESUME_MIN_MS,),
    ).fetchall()

    items = [_add_progress_fields(dict(row)) for row in movie_rows + tv_rows]
    items.sort(
        key=lambda item: (item["updated_at"], item["source_id"]),
        reverse=True,
    )

    result = []
    included_series = set()
    for item in items:
        if item["is_completed"]:
            continue
        if item["media_type"] == "tv":
            if item["media_id"] in included_series:
                continue
            included_series.add(item["media_id"])
        result.append(item)
        if len(result) >= item_limit:
            break

    return result


def _get_started_media_keys(db: sqlite3.Connection):
    movie_ids = db.execute(
        """
        SELECT movie_id
          FROM movie_watch_progress
         WHERE position_ms >= ?
        """,
        (WATCH_RESUME_MIN_MS,),
    ).fetchall()
    series_ids = db.execute(
        """
        SELECT DISTINCT season.series_id
          FROM tv_episode_watch_progress AS progress
               INNER JOIN tv_episode AS episode
               ON episode.episode_id = progress.episode_id
               INNER JOIN tv_season AS season
               ON season.season_id = episode.season_id
         WHERE progress.position_ms >= ?
        """,
        (WATCH_RESUME_MIN_MS,),
    ).fetchall()

    return {
        *(('movie', row["movie_id"]) for row in movie_ids),
        *(('tv', row["series_id"]) for row in series_ids),
    }


def _get_recent_added_items(db: sqlite3.Connection):
    return sorted(
        _get_homepage_movie_items(db) + _get_homepage_tv_items(db),
        key=lambda item: (item["source_id"], item["media_id"]),
        reverse=True,
    )


def _get_unwatched_items(db: sqlite3.Connection, media_items: list[dict]):
    started_media = _get_started_media_keys(db)
    return [
        item
        for item in media_items
        if (item["media_type"], item["media_id"]) not in started_media
    ]


def get_library_page_items(database_path: str | Path, section_name: str):
    """Return every item for a supported full-library page."""
    if section_name not in (
        "recent_added",
        "unwatched",
        "top_rated_movies",
    ):
        raise ValueError("不支持的媒体库板块。")

    with closing(_connect(database_path)) as db:
        if section_name == "top_rated_movies":
            return sorted(
                _get_homepage_movie_items(db),
                key=lambda item: (item["vote_average"], item["media_id"]),
                reverse=True,
            )

        media_items = _get_recent_added_items(db)
        if section_name == "unwatched":
            return _get_unwatched_items(db, media_items)

    return media_items


def get_movie_genre_features(database_path: str | Path):
    """Return every movie genre and its representative backdrop."""
    with closing(_connect(database_path)) as db:
        return _get_movie_genre_features(db)


def _get_favorite_folders(db: sqlite3.Connection):
    rows = db.execute(
        """
        SELECT folder.folder_id,
               folder.folder_name AS name,
               folder.folder_type,
               folder.icon_name AS icon,
               folder.is_system,
               COUNT(item.favorite_item_id) AS count
          FROM favorite_folder AS folder
               LEFT JOIN favorite_item AS item
               ON item.folder_id = folder.folder_id
         GROUP BY folder.folder_id
         ORDER BY folder.folder_position
        """
    ).fetchall()

    return [dict(row) for row in rows]


def get_favorite_page_data(
    database_path: str | Path,
    folder_id: int | None = None,
):
    """Return favorite folders and deduplicated linked media items."""
    with closing(_connect(database_path)) as db:
        folders = _get_favorite_folders(db)
        selected_folder = next(
            (folder for folder in folders if folder["folder_id"] == folder_id),
            None,
        )
        if folder_id is not None and selected_folder is None:
            return None

        parameters = () if folder_id is None else (folder_id,)
        where_clause = "" if folder_id is None else "WHERE folder_id = ?"
        favorite_rows = db.execute(
            f"""
            SELECT favorite_item_id,
                   folder_id,
                   media_type,
                   movie_id,
                   series_id,
                   added_at
              FROM favorite_item
                   {where_clause}
             ORDER BY added_at DESC,
                      favorite_item_id DESC
            """,
            parameters,
        ).fetchall()
        media_items = {
            (item["media_type"], item["media_id"]): item
            for item in (
                _get_homepage_movie_items(db) + _get_homepage_tv_items(db)
            )
        }

    items = []
    included_media = set()
    for favorite in favorite_rows:
        media_id = (
            favorite["movie_id"]
            if favorite["media_type"] == "movie"
            else favorite["series_id"]
        )
        media_key = (favorite["media_type"], media_id)
        if media_key in included_media or media_key not in media_items:
            continue
        item = dict(media_items[media_key])
        item["favorite_added_at"] = favorite["added_at"]
        items.append(item)
        included_media.add(media_key)

    return {
        "folders": folders,
        "selected_folder": selected_folder,
        "items": items,
    }


def is_media_favorite(
    database_path: str | Path,
    media_type: str,
    media_id: int,
):
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")

    media_column = "movie_id" if media_type == "movie" else "series_id"
    folder_id = 1 if media_type == "movie" else 2
    with closing(_connect(database_path)) as db:
        row = db.execute(
            f"""
            SELECT 1
              FROM favorite_item
             WHERE folder_id = ?
               AND {media_column} = ?
            """,
            (folder_id, media_id),
        ).fetchone()

    return row is not None


def toggle_media_favorite(
    database_path: str | Path,
    media_type: str,
    media_id: int,
):
    """Toggle one media item in its movie or TV system folder."""
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")

    media_table = "movie" if media_type == "movie" else "tv_series"
    media_column = "movie_id" if media_type == "movie" else "series_id"
    folder_id = 1 if media_type == "movie" else 2
    with closing(_connect(database_path)) as db:
        with db:
            if db.execute(
                f"SELECT 1 FROM {media_table} WHERE {media_column} = ?",
                (media_id,),
            ).fetchone() is None:
                raise ValueError("媒体不存在。")

            existing = db.execute(
                f"""
                SELECT favorite_item_id
                  FROM favorite_item
                 WHERE folder_id = ?
                   AND {media_column} = ?
                """,
                (folder_id, media_id),
            ).fetchone()
            if existing:
                db.execute(
                    "DELETE FROM favorite_item WHERE favorite_item_id = ?",
                    (existing["favorite_item_id"],),
                )
                return False

            db.execute(
                f"""
                INSERT INTO favorite_item (
                    folder_id,
                    media_type,
                    {media_column}
                )
                     VALUES (?, ?, ?)
                """,
                (folder_id, media_type, media_id),
            )

    return True


def get_homepage_data(database_path: str | Path, item_limit: int = 12):
    """Return real, presentation-ready media data for the library homepage."""
    with closing(_connect(database_path)) as db:
        movie_items = _get_homepage_movie_items(db)
        tv_items = _get_homepage_tv_items(db)
        genre_features = _get_movie_genre_features(db)
        tv_genre_features = _get_tv_genre_features(db)
        continue_watching = _get_continue_watching_items(db, item_limit)
        favorite_folders = _get_favorite_folders(db)
        media_items = sorted(
            movie_items + tv_items,
            key=lambda item: (item["source_id"], item["media_id"]),
            reverse=True,
        )
        unwatched_items = _get_unwatched_items(db, media_items)

    recent_added = media_items[:item_limit]
    top_rated_movies = sorted(
        movie_items,
        key=lambda item: (item["vote_average"], item["media_id"]),
        reverse=True,
    )[:item_limit]
    unwatched = unwatched_items[:item_limit]

    return {
        "continue_watching": continue_watching,
        "favorite_folders": favorite_folders,
        "recent_added": recent_added,
        "genre_features": genre_features,
        "tv_genre_features": tv_genre_features,
        "unwatched": unwatched,
        "top_rated_movies": top_rated_movies,
        "media_count": len(media_items),
        "movie_count": len(movie_items),
        "tv_count": len(tv_items),
    }


def get_media_detail(
    database_path: str | Path,
    media_type: str,
    media_id: int,
):
    """Return one movie or TV series with local artwork and playable sources."""
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")

    with closing(_connect(database_path)) as db:
        if media_type == "movie":
            row = db.execute(
                """
                SELECT 'movie' AS media_type,
                       movie.movie_id AS media_id,
                       movie.title AS name,
                       movie.original_title AS original_name,
                       SUBSTR(movie.release_date, 1, 4) AS year,
                       movie.release_date AS release_date,
                       movie.vote_average AS vote_average,
                       movie.vote_count AS vote_count,
                       movie.runtime AS runtime,
                       movie.overview AS overview,
                       movie.tagline AS tagline,
                       movie.release_status AS media_status,
                       movie.original_language AS original_language,
                       movie.homepage AS homepage,
                       movie.imdb_id AS imdb_id,
                       movie.imdb_rating AS imdb_rating,
                       movie.imdb_vote_count AS imdb_vote_count,
                       movie.rotten_tomatoes_tomatometer AS rotten_tomatoes_tomatometer,
                       movie.rotten_tomatoes_popcornmeter AS rotten_tomatoes_popcornmeter,
                       movie.metacritic_rating AS metacritic_rating,
                       movie.ratings_updated_at AS ratings_updated_at,
                       collection.collection_name AS collection_name,
                       (
                           SELECT image.local_path
                             FROM movie_image AS image
                            WHERE image.movie_id = movie.movie_id
                              AND image.image_type = 'posters'
                            ORDER BY image.image_position
                            LIMIT 1
                       ) AS poster_path,
                       (
                           SELECT image.local_path
                             FROM movie_image AS image
                            WHERE image.movie_id = movie.movie_id
                              AND image.image_type = 'backdrops'
                            ORDER BY image.image_position
                            LIMIT 1
                       ) AS backdrop_path,
                       (
                           SELECT image.local_path
                             FROM movie_image AS image
                            WHERE image.movie_id = movie.movie_id
                              AND image.image_type = 'logos'
                            ORDER BY image.image_position
                            LIMIT 1
                       ) AS logo_path
                  FROM movie
                       LEFT JOIN collection
                       ON collection.collection_id = movie.collection_id
                 WHERE movie.movie_id = ?
                """,
                (media_id,),
            ).fetchone()
            source_rows = db.execute(
                """
                SELECT source.source_id,
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
                       source.probed_at,
                       progress.position_ms,
                       progress.duration_ms,
                       progress.updated_at
                  FROM movie_source AS source
                       LEFT JOIN movie_watch_progress AS progress
                       ON progress.movie_id = source.movie_id
                 WHERE source.movie_id = ?
                 ORDER BY source.source_id
                """,
                (media_id,),
            ).fetchall()
            genre_rows = db.execute(
                """
                SELECT genre.genre_name
                  FROM movie_genre AS relation
                       INNER JOIN genre
                       ON genre.genre_id = relation.genre_id
                 WHERE relation.movie_id = ?
                 ORDER BY relation.genre_position
                """,
                (media_id,),
            ).fetchall()
        else:
            row = db.execute(
                """
                SELECT 'tv' AS media_type,
                       series.series_id AS media_id,
                       series.series_name AS name,
                       series.original_name AS original_name,
                       SUBSTR(series.first_air_date, 1, 4) AS year,
                       series.first_air_date AS release_date,
                       series.vote_average AS vote_average,
                       series.vote_count AS vote_count,
                       NULL AS runtime,
                       series.overview AS overview,
                       series.tagline AS tagline,
                       series.series_status AS media_status,
                       series.last_air_date AS last_air_date,
                       series.in_production AS in_production,
                       series.number_of_seasons AS number_of_seasons,
                       series.number_of_episodes AS number_of_episodes,
                       series.original_language AS original_language,
                       series.homepage AS homepage,
                       series.imdb_id AS imdb_id,
                       series.imdb_rating AS imdb_rating,
                       series.imdb_vote_count AS imdb_vote_count,
                       series.rotten_tomatoes_tomatometer AS rotten_tomatoes_tomatometer,
                       series.rotten_tomatoes_popcornmeter AS rotten_tomatoes_popcornmeter,
                       series.metacritic_rating AS metacritic_rating,
                       series.ratings_updated_at AS ratings_updated_at,
                       series.created_by_json AS created_by_json,
                       series.networks_json AS networks_json,
                       (
                           SELECT image.local_path
                             FROM tv_series_image AS image
                            WHERE image.series_id = series.series_id
                              AND image.image_type = 'posters'
                            ORDER BY image.image_position
                            LIMIT 1
                       ) AS poster_path,
                       (
                           SELECT image.local_path
                             FROM tv_series_image AS image
                            WHERE image.series_id = series.series_id
                              AND image.image_type = 'backdrops'
                            ORDER BY image.image_position
                            LIMIT 1
                       ) AS backdrop_path,
                       (
                           SELECT image.local_path
                             FROM tv_series_image AS image
                            WHERE image.series_id = series.series_id
                              AND image.image_type = 'logos'
                            ORDER BY image.image_position
                            LIMIT 1
                       ) AS logo_path
                  FROM tv_series AS series
                 WHERE series.series_id = ?
                """,
                (media_id,),
            ).fetchone()
            season_rows = db.execute(
                """
                SELECT season_id,
                       season_number,
                       season_name,
                       overview,
                       air_date,
                       episode_count,
                       vote_average
                  FROM tv_season
                 WHERE series_id = ?
                 ORDER BY season_number
                """,
                (media_id,),
            ).fetchall()
            episode_source_rows = db.execute(
                """
                SELECT source.source_id,
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
                       source.probed_at,
                       episode.episode_id,
                       episode.episode_number,
                       episode.episode_name,
                       episode.runtime,
                       episode.air_date,
                       episode.overview,
                       episode.vote_average,
                       episode.vote_count,
                       episode.imdb_id,
                       episode.imdb_rating,
                       episode.imdb_rating_updated_at,
                       season.season_id,
                       season.season_number,
                       image.local_path AS still_path,
                       progress.position_ms,
                       progress.duration_ms,
                       progress.updated_at
                  FROM tv_episode AS episode
                       INNER JOIN tv_season AS season
                       ON season.season_id = episode.season_id
                       LEFT JOIN tv_episode_source AS source
                       ON source.episode_id = episode.episode_id
                       LEFT JOIN tv_episode_image AS image
                       ON image.episode_id = episode.episode_id
                       LEFT JOIN tv_episode_watch_progress AS progress
                       ON progress.episode_id = episode.episode_id
                 WHERE season.series_id = ?
                 ORDER BY season_number,
                          episode_number,
                          source.source_id
                """,
                (media_id,),
            ).fetchall()
            genre_rows = db.execute(
                """
                SELECT genre.genre_name
                  FROM tv_series_genre AS relation
                       INNER JOIN genre
                       ON genre.genre_id = relation.genre_id
                 WHERE relation.series_id = ?
                 ORDER BY relation.genre_position
                """,
                (media_id,),
            ).fetchall()

        relation_config = MEDIA_RELATIONS[media_type]
        owner_column = relation_config["owner_column"]
        production_country_rows = db.execute(
            f"""
            SELECT country.country_name,
                   country.country_code
              FROM {relation_config['production_country_table']} AS relation
                   INNER JOIN country
                   ON country.country_code = relation.country_code
             WHERE relation.{owner_column} = ?
             ORDER BY relation.country_position
            """,
            (media_id,),
        ).fetchall()
        language_rows = db.execute(
            f"""
            SELECT language.native_name,
                   language.english_name,
                   language.language_code
              FROM {relation_config['spoken_language_table']} AS relation
                   INNER JOIN spoken_language AS language
                   ON language.language_code = relation.language_code
             WHERE relation.{owner_column} = ?
             ORDER BY relation.language_position
            """,
            (media_id,),
        ).fetchall()
        company_rows = db.execute(
            f"""
            SELECT company.company_name
              FROM {relation_config['production_company_table']} AS relation
                   INNER JOIN production_company AS company
                   ON company.company_id = relation.company_id
             WHERE relation.{owner_column} = ?
             ORDER BY relation.company_position
            """,
            (media_id,),
        ).fetchall()

    if row is None:
        return None

    detail = dict(row)
    detail["genres"] = [genre["genre_name"] for genre in genre_rows]
    detail["production_countries"] = [
        country["country_name"] or country["country_code"]
        for country in production_country_rows
    ]
    detail["spoken_languages"] = [
        language["native_name"]
        or language["english_name"]
        or language["language_code"]
        for language in language_rows
    ]
    detail["production_companies"] = [
        company["company_name"]
        for company in company_rows
    ]
    detail["age_rating"] = None

    if media_type == "movie":
        detail["sources"] = []
        for row_source in source_rows:
            source = _add_progress_fields(dict(row_source))
            source["source_label"] = Path(source["alist_path"]).name
            detail["sources"].append(source)
        detail["seasons"] = []
        detail["creators"] = []
        detail["networks"] = []
    else:
        creators = json.loads(detail.pop("created_by_json") or "[]")
        networks = json.loads(detail.pop("networks_json") or "[]")
        detail["creators"] = [
            creator["name"]
            for creator in creators
            if creator.get("name")
        ]
        detail["networks"] = [
            network["name"]
            for network in networks
            if network.get("name")
        ]

        seasons = []
        seasons_by_id = {}
        for season_row in season_rows:
            season = dict(season_row)
            season["episodes"] = []
            seasons.append(season)
            seasons_by_id[season["season_id"]] = season

        episodes_by_id = {}
        sources = []
        for episode_source_row in episode_source_rows:
            source_row = dict(episode_source_row)
            episode_id = source_row["episode_id"]
            episode = episodes_by_id.get(episode_id)
            if episode is None:
                episode = _add_progress_fields(
                    {
                        key: source_row[key]
                        for key in (
                            "episode_id",
                            "episode_number",
                            "episode_name",
                            "runtime",
                            "air_date",
                            "overview",
                            "vote_average",
                            "vote_count",
                            "imdb_id",
                            "imdb_rating",
                            "imdb_rating_updated_at",
                            "season_number",
                            "still_path",
                            "position_ms",
                            "duration_ms",
                            "updated_at",
                        )
                    }
                )
                episode["sources"] = []
                episodes_by_id[episode_id] = episode
                seasons_by_id[source_row["season_id"]]["episodes"].append(episode)

            if source_row["source_id"] is not None:
                source = _add_progress_fields(source_row)
                source["source_label"] = Path(source["alist_path"]).name
                episode["sources"].append(source)
                sources.append(source)

        detail["sources"] = sources
        detail["seasons"] = seasons

    return detail


def get_media_overview_data(
    database_path: str | Path,
    query: str | None = None,
    media_type: str = "all",
):
    """Return aggregated overview metadata and overall library stats."""
    if media_type not in ("all", "movie", "tv"):
        media_type = "all"

    with closing(_connect(database_path)) as db:
        stats_row = db.execute(
            """
            SELECT (SELECT COUNT(*) FROM movie) AS movie_count,
                   (SELECT COUNT(*) FROM tv_series) AS tv_count,
                   (SELECT COUNT(*) FROM tv_season) AS season_count,
                   (SELECT COUNT(*) FROM tv_episode) AS episode_count,
                   ((SELECT COUNT(*) FROM movie_source) + (SELECT COUNT(*) FROM tv_episode_source)) AS source_count,
                   ((SELECT COUNT(*) FROM movie_image) + (SELECT COUNT(*) FROM tv_series_image) + (SELECT COUNT(*) FROM tv_episode_image)) AS image_count
            """
        ).fetchone()
        stats = dict(stats_row) if stats_row else {}
        stats["media_count"] = stats.get("movie_count", 0) + stats.get("tv_count", 0)

        movie_rows = []
        if media_type in ("all", "movie"):
            movie_rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT 'movie' AS media_type,
                           movie.movie_id AS media_id,
                           movie.title AS name,
                           movie.original_title AS original_name,
                           SUBSTR(movie.release_date, 1, 4) AS year,
                           movie.release_date AS release_date,
                           movie.vote_average AS vote_average,
                           movie.vote_count AS vote_count,
                           movie.overview AS overview,
                           movie.runtime AS runtime,
                           movie.release_status AS media_status,
                           (
                               SELECT COUNT(*)
                                 FROM movie_source AS source
                                WHERE source.movie_id = movie.movie_id
                           ) AS source_count,
                           (
                               SELECT COUNT(*)
                                 FROM movie_image AS image
                                WHERE image.movie_id = movie.movie_id
                           ) AS image_count,
                           (
                               SELECT image.local_path
                                 FROM movie_image AS image
                                WHERE image.movie_id = movie.movie_id
                                  AND image.image_type = 'posters'
                                ORDER BY image.image_position
                                LIMIT 1
                           ) AS poster_path,
                           (
                               SELECT image.local_path
                                 FROM movie_image AS image
                                WHERE image.movie_id = movie.movie_id
                                  AND image.image_type = 'backdrops'
                                ORDER BY image.image_position
                                LIMIT 1
                           ) AS backdrop_path,
                           (
                               SELECT image.local_path
                                 FROM movie_image AS image
                                WHERE image.movie_id = movie.movie_id
                                  AND image.image_type = 'logos'
                                ORDER BY image.image_position
                                LIMIT 1
                           ) AS logo_path,
                           (
                               SELECT GROUP_CONCAT(genre.genre_name, ' / ')
                                 FROM movie_genre AS relation
                                      INNER JOIN genre
                                      ON genre.genre_id = relation.genre_id
                                WHERE relation.movie_id = movie.movie_id
                           ) AS genres_text,
                           (
                               SELECT MAX(source.source_id)
                                 FROM movie_source AS source
                                WHERE source.movie_id = movie.movie_id
                           ) AS source_id
                      FROM movie
                     ORDER BY source_id DESC NULLS LAST,
                              movie.movie_id DESC
                    """
                ).fetchall()
            ]

        tv_rows = []
        if media_type in ("all", "tv"):
            tv_rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT 'tv' AS media_type,
                           series.series_id AS media_id,
                           series.series_name AS name,
                           series.original_name AS original_name,
                           SUBSTR(series.first_air_date, 1, 4) AS year,
                           series.first_air_date AS release_date,
                           series.vote_average AS vote_average,
                           series.vote_count AS vote_count,
                           series.overview AS overview,
                           series.series_status AS media_status,
                           series.number_of_seasons AS season_count,
                           series.number_of_episodes AS episode_count,
                           (
                               SELECT COUNT(*)
                                 FROM tv_episode_source AS source
                                      INNER JOIN tv_episode AS episode
                                      ON episode.episode_id = source.episode_id
                                      INNER JOIN tv_season AS season
                                      ON season.season_id = episode.season_id
                                WHERE season.series_id = series.series_id
                           ) AS source_count,
                           (
                               SELECT COUNT(*)
                                 FROM tv_series_image AS image
                                WHERE image.series_id = series.series_id
                           ) AS image_count,
                           (
                               SELECT image.local_path
                                 FROM tv_series_image AS image
                                WHERE image.series_id = series.series_id
                                  AND image.image_type = 'posters'
                                ORDER BY image.image_position
                                LIMIT 1
                           ) AS poster_path,
                           (
                               SELECT image.local_path
                                 FROM tv_series_image AS image
                                WHERE image.series_id = series.series_id
                                  AND image.image_type = 'backdrops'
                                ORDER BY image.image_position
                                LIMIT 1
                           ) AS backdrop_path,
                           (
                               SELECT image.local_path
                                 FROM tv_series_image AS image
                                WHERE image.series_id = series.series_id
                                  AND image.image_type = 'logos'
                                ORDER BY image.image_position
                                LIMIT 1
                           ) AS logo_path,
                           (
                               SELECT GROUP_CONCAT(genre.genre_name, ' / ')
                                 FROM tv_series_genre AS relation
                                      INNER JOIN genre
                                      ON genre.genre_id = relation.genre_id
                                WHERE relation.series_id = series.series_id
                           ) AS genres_text,
                           (
                               SELECT MAX(source.source_id)
                                 FROM tv_episode_source AS source
                                      INNER JOIN tv_episode AS episode
                                      ON episode.episode_id = source.episode_id
                                      INNER JOIN tv_season AS season
                                      ON season.season_id = episode.season_id
                                WHERE season.series_id = series.series_id
                           ) AS source_id
                      FROM tv_series AS series
                     ORDER BY source_id DESC NULLS LAST,
                              series.series_id DESC
                    """
                ).fetchall()
            ]

        image_rows = db.execute(
            """
            SELECT 'movie' AS media_type,
                   movie_id AS media_id,
                   image_type,
                   image_position,
                   local_path
              FROM movie_image

             UNION ALL

            SELECT 'tv' AS media_type,
                   series_id AS media_id,
                   image_type,
                   image_position,
                   local_path
              FROM tv_series_image
             ORDER BY media_type,
                      media_id,
                      image_type,
                      image_position
            """
        ).fetchall()

    all_items = sorted(
        movie_rows + tv_rows,
        key=lambda item: (item["source_id"] or 0, item["media_id"]),
        reverse=True,
    )

    image_options = {}
    for image_row in image_rows:
        image = dict(image_row)
        media_key = (image.pop("media_type"), image.pop("media_id"))
        media_images = image_options.setdefault(
            media_key,
            {"backdrops": [], "posters": [], "logos": []},
        )
        media_images[image["image_type"]].append(image)

    for item in all_items:
        genres_str = item.get("genres_text") or ""
        item["genres"] = [g.strip() for g in genres_str.split("/") if g.strip()]
        item["vote_count_label"] = f"{item['vote_count']:,}" if item.get("vote_count") else "0"
        item["image_options"] = image_options.get(
            (item["media_type"], item["media_id"]),
            {"backdrops": [], "posters": [], "logos": []},
        )

    filtered_items = all_items
    if query:
        q = query.strip().casefold()
        filtered_items = [
            item
            for item in all_items
            if q in str(item["media_id"])
            or q in str(item.get("name") or "").casefold()
            or q in str(item.get("original_name") or "").casefold()
        ]

    return {
        "stats": stats,
        "media_items": filtered_items,
        "total_filtered": len(filtered_items),
        "query": query or "",
        "media_type": media_type,
    }


def save_media_ratings(
    database_path: str | Path,
    media_type: str,
    media_id: int,
    ratings: dict,
):
    """Save or update external ratings for one movie or TV series."""
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")

    table_name = "movie" if media_type == "movie" else "tv_series"
    id_column = "movie_id" if media_type == "movie" else "series_id"

    values = {
        "media_id": media_id,
        "imdb_id": ratings.get("imdb_id"),
        "imdb_rating": ratings.get("imdb_rating"),
        "imdb_vote_count": ratings.get("imdb_vote_count"),
        "rotten_tomatoes_tomatometer": ratings.get("rotten_tomatoes_tomatometer"),
        "rotten_tomatoes_popcornmeter": ratings.get("rotten_tomatoes_popcornmeter"),
        "metacritic_rating": ratings.get("metacritic_rating"),
        "ratings_updated_at": ratings.get("ratings_updated_at"),
    }
    with closing(_connect(database_path)) as db:
        with db:
            cursor = db.execute(
                f"""
                UPDATE {table_name}
                   SET imdb_id = COALESCE(:imdb_id, {table_name}.imdb_id),
                       imdb_rating = COALESCE(
                           :imdb_rating,
                           {table_name}.imdb_rating
                       ),
                       imdb_vote_count = COALESCE(
                           :imdb_vote_count,
                           {table_name}.imdb_vote_count
                       ),
                       rotten_tomatoes_tomatometer = COALESCE(
                           :rotten_tomatoes_tomatometer,
                           {table_name}.rotten_tomatoes_tomatometer
                       ),
                       rotten_tomatoes_popcornmeter = COALESCE(
                           :rotten_tomatoes_popcornmeter,
                           {table_name}.rotten_tomatoes_popcornmeter
                       ),
                       metacritic_rating = COALESCE(
                           :metacritic_rating,
                           {table_name}.metacritic_rating
                       ),
                       ratings_updated_at = COALESCE(:ratings_updated_at, CURRENT_TIMESTAMP)
                 WHERE {id_column} = :media_id
                """,
                values,
            )
            if cursor.rowcount == 0:
                raise ValueError("媒体不存在。")


def has_media_ratings(
    database_path: str | Path,
    media_type: str,
    media_id: int,
    max_age_days: int = 30,
) -> bool:
    """Return True when the media rating cache is present and still fresh."""
    if media_type not in ("movie", "tv"):
        raise ValueError("不支持的媒体类型。")
    if max_age_days <= 0:
        raise ValueError("评分缓存有效期必须大于 0 天。")

    table_name = "movie" if media_type == "movie" else "tv_series"
    id_column = "movie_id" if media_type == "movie" else "series_id"

    with closing(_connect(database_path)) as db:
        row = db.execute(
            f"""
            SELECT 1
              FROM {table_name}
             WHERE {id_column} = ?
               AND ratings_updated_at IS NOT NULL
               AND julianday(ratings_updated_at) >= julianday('now', ?)
            """,
            (media_id, f"-{int(max_age_days)} days"),
        ).fetchone()
        return bool(row)


def has_series_ratings(
    database_path: str | Path,
    series_id: int,
    max_age_days: int = 30,
) -> bool:
    """Return True if the TV series rating cache is still fresh."""
    return has_media_ratings(database_path, "tv", series_id, max_age_days)


def has_movie_ratings(
    database_path: str | Path,
    movie_id: int,
    max_age_days: int = 30,
) -> bool:
    """Return True if the movie rating cache is still fresh."""
    return has_media_ratings(database_path, "movie", movie_id, max_age_days)


def get_series_imdb_id(database_path: str | Path, series_id: int) -> str | None:
    """Return the stored imdb_id for a TV series, or None."""
    with closing(_connect(database_path)) as db:
        row = db.execute(
            "SELECT imdb_id FROM tv_series WHERE series_id = ?",
            (series_id,),
        ).fetchone()
        return row[0] if row and row[0] else None


def has_season_episode_ratings(
    database_path: str | Path,
    series_id: int,
    season_number: int,
    max_age_days: int = 30,
) -> bool:
    """Return True when every local episode has a fresh IMDb sync timestamp."""
    if max_age_days <= 0:
        raise ValueError("评分缓存有效期必须大于 0 天。")

    with closing(_connect(database_path)) as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS episode_count,
                   SUM(
                       CASE
                           WHEN episode.imdb_rating_updated_at IS NOT NULL
                            AND julianday(episode.imdb_rating_updated_at) >= julianday('now', ?)
                           THEN 1
                           ELSE 0
                       END
                   ) AS fresh_count
              FROM tv_episode AS episode
                   INNER JOIN tv_season AS season
                   ON season.season_id = episode.season_id
             WHERE season.series_id = ?
               AND season.season_number = ?
            """,
            (f"-{int(max_age_days)} days", series_id, season_number),
        ).fetchone()
        return bool(
            row
            and row["episode_count"] > 0
            and row["fresh_count"] == row["episode_count"]
        )


def save_season_episode_ratings(
    database_path: str | Path,
    series_id: int,
    season_number: int,
    episode_ratings: list[dict],
    updated_at: str | None = None,
) -> int:
    """Save IMDb ratings for all episodes of a season in tv_episode table."""
    with closing(_connect(database_path)) as db:
        with db:
            season_row = db.execute(
                "SELECT season_id FROM tv_season WHERE series_id = ? AND season_number = ?",
                (series_id, season_number),
            ).fetchone()
            if not season_row:
                return 0
            season_id = season_row[0]

            updated_count = 0
            for ep in episode_ratings:
                ep_num = ep.get("episode_number")
                if ep_num is None:
                    continue
                cursor = db.execute(
                    """
                    UPDATE tv_episode
                       SET imdb_id = COALESCE(:imdb_id, imdb_id),
                           imdb_rating = COALESCE(:imdb_rating, imdb_rating),
                           imdb_rating_updated_at = COALESCE(:updated_at, CURRENT_TIMESTAMP)
                     WHERE season_id = :season_id
                       AND episode_number = :episode_number
                    """,
                    {
                        "imdb_id": ep.get("imdb_id"),
                        "imdb_rating": ep.get("imdb_rating"),
                        "updated_at": updated_at,
                        "season_id": season_id,
                        "episode_number": ep_num,
                    },
                )
                if cursor.rowcount > 0:
                    updated_count += cursor.rowcount

            return updated_count
