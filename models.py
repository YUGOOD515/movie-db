import sqlite3
from contextlib import closing
from pathlib import Path


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


def save_movie(
    database_path: str | Path,
    details: dict,
    images: list[dict],
    alist_path: str,
):
    """Persist TMDB details, local images and an AList mapping atomically."""
    with closing(_connect(database_path)) as db:
        with db:
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

    for table_name in (
        "movie_genre",
        "movie_origin_country",
        "movie_production_country",
        "movie_production_company",
        "movie_spoken_language",
        "movie_image",
    ):
        db.execute(f"DELETE FROM {table_name} WHERE movie_id = ?", (movie_id,))

    for position, genre in enumerate(details.get("genres", []), 1):
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
            """
            INSERT INTO movie_genre (movie_id, genre_id, genre_position)
                 VALUES (?, ?, ?)
            """,
            (movie_id, genre["id"], position),
        )

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

    for position, country_code in enumerate(origin_countries, 1):
        db.execute(
            """
            INSERT INTO movie_origin_country (
                movie_id,
                country_code,
                country_position
            )
                 VALUES (?, ?, ?)
            """,
            (movie_id, country_code, position),
        )

    for position, country in enumerate(details.get("production_countries", []), 1):
        db.execute(
            """
            INSERT INTO movie_production_country (
                movie_id,
                country_code,
                country_position
            )
                 VALUES (?, ?, ?)
            """,
            (movie_id, country["iso_3166_1"], position),
        )

    for position, company in enumerate(companies, 1):
        db.execute(
            """
            INSERT INTO production_company (
                company_id,
                company_name,
                origin_country_code,
                logo_path
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
            """
            INSERT INTO movie_production_company (
                movie_id,
                company_id,
                company_position
            )
                 VALUES (?, ?, ?)
            """,
            (movie_id, company["id"], position),
        )

    for position, language in enumerate(details.get("spoken_languages", []), 1):
        db.execute(
            """
            INSERT INTO spoken_language (
                language_code,
                english_name,
                native_name
            )
                 VALUES (?, ?, ?)
            ON CONFLICT (language_code)
            DO UPDATE SET english_name = excluded.english_name,
                          native_name = excluded.native_name
            """,
            (
                language["iso_639_1"],
                language["english_name"],
                language["name"],
            ),
        )
        db.execute(
            """
            INSERT INTO movie_spoken_language (
                movie_id,
                language_code,
                language_position
            )
                 VALUES (?, ?, ?)
            """,
            (movie_id, language["iso_639_1"], position),
        )

    for image in images:
        db.execute(
            """
            INSERT INTO movie_image (
                movie_id,
                image_type,
                image_position,
                image_url,
                local_path
            )
                 VALUES (?, ?, ?, ?, ?)
            """,
            (
                movie_id,
                image["image_type"],
                image["image_position"],
                image["image_url"],
                image["local_path"],
            ),
        )

    db.execute(
        """
        INSERT INTO movie_source (movie_id, alist_path)
             VALUES (?, ?)
        ON CONFLICT (alist_path)
        DO UPDATE SET movie_id = excluded.movie_id
        """,
        (movie_id, alist_path),
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
