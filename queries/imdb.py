from sqlite3 import Connection


TITLE_TYPE_MAP = {
    "movie": "movie",
    "tvMovie": "movie",
    "video": "movie",
    "tvSeries": "series",
    "tvMiniSeries": "series",
    "tvEpisode": "episode",
}


def _dictionary(row) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["title_type"] = TITLE_TYPE_MAP.get(item.pop("imdb_title_type"), "")
    return item


def _title_select() -> str:
    return """
        SELECT basics.tconst AS title_id,
               basics.titleType AS imdb_title_type,
               basics.primaryTitle AS primary_title,
               basics.originalTitle AS original_title,
               basics.isAdult AS is_adult,
               basics.startYear AS start_year,
               basics.endYear AS end_year,
               basics.runtimeMinutes AS runtime_minutes,
               basics.genres,
               ratings.averageRating AS average_rating,
               ratings.numVotes AS num_votes,
               COALESCE(
                   (
                       SELECT alias.title
                         FROM title_akas AS alias
                        WHERE alias.titleId = basics.tconst
                          AND (
                              alias.region IN ('CN', 'TW', 'HK')
                              OR alias.language = 'zh'
                          )
                        ORDER BY CASE alias.region
                                     WHEN 'CN' THEN 0
                                     WHEN 'TW' THEN 1
                                     WHEN 'HK' THEN 2
                                     ELSE 3
                                 END,
                                 alias.ordering
                        LIMIT 1
                   ),
                   basics.primaryTitle,
                   basics.originalTitle,
                   basics.tconst
               ) AS display_title
          FROM title_basics AS basics
          LEFT JOIN title_ratings AS ratings
            ON ratings.tconst = basics.tconst
    """


def get_imdb_title(db: Connection, title_id: str) -> dict | None:
    row = db.execute(
        _title_select() + " WHERE basics.tconst = ?",
        (title_id,),
    ).fetchone()
    return _dictionary(row)


def get_episode_relation(db: Connection, episode_id: str) -> dict | None:
    row = db.execute(
        """
        SELECT tconst AS episode_id,
               parentTconst AS series_id,
               seasonNumber AS season_number,
               episodeNumber AS episode_number
          FROM title_episode
         WHERE tconst = ?
        """,
        (episode_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_episode_by_position(
    db: Connection,
    series_id: str,
    season_number: int,
    episode_number: int,
) -> dict | None:
    row = db.execute(
        """
        SELECT tconst AS episode_id,
               parentTconst AS series_id,
               seasonNumber AS season_number,
               episodeNumber AS episode_number
          FROM title_episode
         WHERE parentTconst = ?
           AND seasonNumber = ?
           AND episodeNumber = ?
        """,
        (series_id, season_number, episode_number),
    ).fetchone()
    return dict(row) if row is not None else None


def get_series_episodes(db: Connection, series_id: str) -> list[dict]:
    rows = db.execute(
        _title_select()
        + """
          JOIN title_episode AS episode
            ON episode.tconst = basics.tconst
         WHERE episode.parentTconst = ?
         ORDER BY episode.seasonNumber,
                  episode.episodeNumber,
                  basics.tconst
        """,
        (series_id,),
    ).fetchall()
    episodes = []
    for row in rows:
        item = _dictionary(row)
        relation = get_episode_relation(db, item["title_id"])
        item.update(relation)
        episodes.append(item)
    return episodes

