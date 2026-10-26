"""Read the source ownership and series context used by media discovery."""


def get_associated_paths(db) -> dict[str, dict]:
    rows = db.execute("""
        SELECT media.alist_path,
               COALESCE(series.title_id, title.title_id) AS title_id,
               COALESCE(series.display_title, title.display_title) AS name,
               COALESCE(series.start_year, title.start_year) AS year,
               COALESCE(series.title_type, title.title_type) AS title_type,
               relation.season_number
          FROM media_file AS media
          JOIN title_basic AS title ON title.title_id = media.title_id
          LEFT JOIN title_relation AS relation ON relation.episode_id = title.title_id
          LEFT JOIN title_basic AS series ON series.title_id = relation.series_id
    """).fetchall()
    return {row["alist_path"]: dict(row) for row in rows}


def get_series_episodes(db, series_id: str) -> list[dict]:
    """库内已有的单集季集关系，录入时优先沿用。"""
    rows = db.execute("""
        SELECT relation.episode_id AS title_id,
               relation.season_number,
               relation.episode_number,
               title.display_title
          FROM title_relation AS relation
          JOIN title_basic AS title ON title.title_id = relation.episode_id
         WHERE relation.series_id = ?
         ORDER BY relation.season_number, relation.episode_number
    """, (series_id,)).fetchall()
    return [dict(row) for row in rows]


def get_file_targets(db, paths) -> dict[str, dict]:
    paths = list(paths)
    if not paths:
        return {}
    rows = db.execute(f"""
        SELECT media.alist_path, media.title_id, relation.series_id,
               relation.season_number, relation.episode_number
          FROM media_file AS media
          LEFT JOIN title_relation AS relation ON relation.episode_id = media.title_id
         WHERE media.alist_path IN ({','.join('?' for _ in paths)})
    """, paths).fetchall()
    return {row["alist_path"]: dict(row) for row in rows}


def get_existing_artwork_slots(db, title_ids: list[str]) -> set[tuple[str, str, int]]:
    if not title_ids:
        return set()
    placeholders = ", ".join("?" for _ in title_ids)
    return {
        tuple(row)
        for row in db.execute(
            f"""
            SELECT title_id, artwork_type, position
              FROM title_artwork
             WHERE title_id IN ({placeholders})
            """,
            tuple(title_ids),
        )
    }


def get_douban_id(db, title_id: str) -> str | None:
    try:
        row = db.execute("SELECT douban_id FROM title_basic WHERE title_id = ?", (title_id,)).fetchone()
    except Exception:  # 旧媒体库还没有这一列
        return None
    return row[0] if row else None


def get_recognition_titles(db) -> list[dict]:
    rows = db.execute("""
        SELECT title_id, display_title, primary_title, original_title, start_year, title_type, tmdb_id
          FROM title_basic WHERE title_type IN ('movie', 'series')
    """).fetchall()
    return [dict(row) for row in rows]
