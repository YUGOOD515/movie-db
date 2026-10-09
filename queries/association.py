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
