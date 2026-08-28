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
MEDIA_SOURCES = {
    "movie": ("movie_source", "movie_id"),
    "tv": ("tv_episode_source", "episode_id"),
}


def _connect(database_path: str | Path):
    connection = sqlite3.connect(database_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


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
    db.execute(
        f"DELETE FROM {table_name} WHERE {owner_column} = ?",
        (owner_id,),
    )
    for image in images:
        db.execute(
            f"""
            INSERT INTO {table_name} (
                {owner_column}, image_type, image_position, image_url, local_path
            )
                 VALUES (?, ?, ?, ?, ?)
            """,
            (
                owner_id,
                image["image_type"],
                image["image_position"],
                image["image_url"],
                image["local_path"],
            ),
        )


def save_movie(
    database_path: str | Path,
    details: dict,
    images: list[dict],
    alist_path: str,
):
    """Persist TMDB details, local images and an AList mapping atomically."""
    with closing(_connect(database_path)) as db:
        with db:
            _assert_source_available(db, "movie", alist_path)
            _save_movie(db, details, images, alist_path)


def _save_movie(
    db: sqlite3.Connection,
    details: dict,
    images: list[dict],
    alist_path: str,
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
    _upsert_source(db, "movie", movie_id, alist_path)


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
            )


def _save_tv_episode(
    db: sqlite3.Connection,
    series_details: dict,
    season_details: dict,
    episode_number: int,
    alist_path: str,
    series_images: list[dict] | None,
    episode_still: dict | None,
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
    _upsert_source(db, "tv", selected_episode_id, alist_path)

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
               source.source_id AS source_order,
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
               ) AS backdrop_path
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
               linked.source_id AS source_order,
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
               ) AS backdrop_path
          FROM tv_series AS series
               INNER JOIN linked_episode AS linked
               ON linked.series_id = series.series_id
              AND linked.source_rank = 1
         ORDER BY linked.source_id DESC
        """
    ).fetchall()

    return [dict(row) for row in rows]


def _get_movie_genre_features(db: sqlite3.Connection):
    rows = db.execute(
        """
        WITH ranked_genre AS (
            SELECT genre.genre_id,
                   genre.genre_name,
                   movie.movie_id,
                   movie.title,
                   ROW_NUMBER() OVER (
                       PARTITION BY genre.genre_id
                           ORDER BY movie.vote_average DESC,
                                    movie.movie_id DESC
                   ) AS media_rank
              FROM movie_genre AS relation
                   INNER JOIN genre
                   ON genre.genre_id = relation.genre_id
                   INNER JOIN movie
                   ON movie.movie_id = relation.movie_id
        )
        SELECT ranked.genre_id,
               ranked.genre_name,
               ranked.movie_id AS media_id,
               ranked.title AS media_name,
               (
                   SELECT image.local_path
                     FROM movie_image AS image
                    WHERE image.movie_id = ranked.movie_id
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


def get_homepage_data(database_path: str | Path, item_limit: int = 12):
    """Return real, presentation-ready media data for the library homepage."""
    with closing(_connect(database_path)) as db:
        movie_items = _get_homepage_movie_items(db)
        tv_items = _get_homepage_tv_items(db)
        genre_features = _get_movie_genre_features(db)

    media_items = movie_items + tv_items
    recent_added = sorted(
        media_items,
        key=lambda item: (item["source_order"], item["media_id"]),
        reverse=True,
    )[:item_limit]
    top_rated_movies = sorted(
        movie_items,
        key=lambda item: (item["vote_average"], item["media_id"]),
        reverse=True,
    )[:item_limit]

    return {
        "continue_watching": [],
        "favorite_folders": [
            {"name": "电影收藏", "icon": "film", "count": 0},
            {"name": "电视剧收藏", "icon": "tv", "count": 0},
            {"name": "自定义收藏", "icon": "bookmark-star", "count": 0},
        ],
        "recent_added": recent_added,
        "genre_features": genre_features,
        "unwatched": recent_added,
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
                 WHERE movie.movie_id = ?
                """,
                (media_id,),
            ).fetchone()
            source_rows = db.execute(
                """
                SELECT source_id,
                       alist_path
                  FROM movie_source
                 WHERE movie_id = ?
                 ORDER BY source_id
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
            source_rows = db.execute(
                """
                WITH ranked_sources AS (
                    SELECT source.source_id,
                           source.alist_path,
                           episode.episode_id,
                           episode.episode_number,
                           episode.episode_name,
                           episode.runtime,
                           episode.air_date,
                           episode.overview,
                           season.season_number,
                           image.local_path AS still_path,
                           ROW_NUMBER() OVER (
                               PARTITION BY episode.episode_id
                               ORDER BY source.source_id DESC
                           ) AS source_rank
                      FROM tv_episode_source AS source
                           INNER JOIN tv_episode AS episode
                           ON episode.episode_id = source.episode_id
                           INNER JOIN tv_season AS season
                           ON season.season_id = episode.season_id
                           LEFT JOIN tv_episode_image AS image
                           ON image.episode_id = episode.episode_id
                     WHERE season.series_id = ?
                )
                SELECT source_id,
                       alist_path,
                       episode_id,
                       episode_number,
                       episode_name,
                       runtime,
                       air_date,
                       overview,
                       season_number,
                       still_path
                  FROM ranked_sources
                 WHERE source_rank = 1
                 ORDER BY season_number,
                          episode_number
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

    if row is None:
        return None

    detail = dict(row)
    detail["sources"] = [dict(source) for source in source_rows]
    detail["genres"] = [genre["genre_name"] for genre in genre_rows]
    detail["age_rating"] = None

    return detail
