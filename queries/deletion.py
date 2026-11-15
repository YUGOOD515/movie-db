"""Read the complete local ownership of a movie or series."""


def query_title_deletion(db, title_id):
    title = db.execute("SELECT * FROM title_basic WHERE title_id = ?", (title_id,)).fetchone()
    if title is None:
        raise ValueError("作品不存在或已删除。")
    if title["title_type"] == "episode":
        relation = db.execute("SELECT series_id FROM title_relation WHERE episode_id = ?", (title_id,)).fetchone()
        if relation is None:
            raise ValueError("找不到该单集所属的剧集。")
        return query_title_deletion(db, relation[0])
    ids = [title["title_id"]]
    if title["title_type"] == "series":
        ids += [row[0] for row in db.execute(
            "SELECT episode_id FROM title_relation WHERE series_id = ? ORDER BY episode_id", (title_id,)
        )]
    placeholders = ",".join("?" for _ in ids)
    records = {}
    for table in ("title_basic", "title_artwork", "title_cast", "media_file", "collection_item"):
        records[table] = [dict(row) for row in db.execute(
            f"SELECT * FROM {table} WHERE title_id IN ({placeholders}) ORDER BY rowid", ids
        )]
    records["title_relation"] = [dict(row) for row in db.execute(
        f"SELECT * FROM title_relation WHERE episode_id IN ({placeholders}) ORDER BY episode_id", ids
    )]
    records["playback_progress"] = [dict(row) for row in db.execute(
        f"SELECT p.* FROM playback_progress AS p JOIN media_file AS m ON m.file_id = p.file_id "
        f"WHERE m.title_id IN ({placeholders}) ORDER BY p.playback_id", ids
    )]
    # 只取本作品的图片引用，以及别的作品里指向同一文件或本作品图片目录的引用（用来判断共用）；
    # 不再读全库引用，否则删除前的逐个文件检查会随媒体库变大而越来越慢。
    folders = " OR ".join("i.local_path LIKE ?" for _ in ids)
    references = [dict(row) for row in db.execute(
        "WITH image_reference AS ("
        "SELECT title_id, local_path FROM title_artwork UNION ALL "
        "SELECT title_id, local_path FROM title_cast WHERE local_path IS NOT NULL), "
        f"owned AS (SELECT local_path FROM image_reference WHERE title_id IN ({placeholders})) "
        "SELECT i.title_id, i.local_path, COALESCE(s.title_id, t.title_id, i.title_id) AS work_id, "
        "COALESCE(s.display_title, t.display_title, i.title_id) AS work_name "
        "FROM image_reference AS i LEFT JOIN title_basic AS t ON t.title_id = i.title_id "
        "LEFT JOIN title_relation AS r ON r.episode_id = t.title_id "
        "LEFT JOIN title_basic AS s ON s.title_id = r.series_id "
        f"WHERE i.title_id IN ({placeholders}) "
        "OR i.local_path COLLATE NOCASE IN (SELECT local_path FROM owned) "
        f"OR {folders}",
        [*ids, *ids, *(f"images/{value}/%" for value in ids)]
    )]
    collections = [dict(row) for row in db.execute(
        f"SELECT DISTINCT c.name, c.icon_name FROM collection AS c "
        f"JOIN collection_item AS i ON i.collection_id = c.collection_id "
        f"WHERE i.title_id IN ({placeholders}) ORDER BY c.\"order\", c.collection_id", ids
    )]
    return {"title": dict(title), "ids": ids, "records": records, "references": references, "collections": collections}
