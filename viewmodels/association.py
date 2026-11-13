"""关联工作区的已入库文件；不将扫描推测当成实际关联。"""

from pathlib import PurePosixPath

from flask import url_for


def build_associated_viewmodel(rows: list[dict]) -> dict:
    records = {}
    for row in rows:
        key = row["title_id"]
        if key not in records:
            records[key] = {
                "title_id": key,
                "name": row["name"],
                "year": row["year"],
                "media_type": "tv" if row["title_type"] == "series" else "movie",
                "tmdb_id": row["tmdb_id"],
                "detail_url": url_for("title_detail", title_id=key),
                "files": [],
            }
        season, episode = row["season_number"], row["episode_number"]
        position = f"S{season:02d}E{episode:02d}" if season is not None and episode is not None else "电影"
        records[key]["files"].append({
            "path": row["path"],
            "name": PurePosixPath(row["path"]).name,
            "title_id": row["file_title_id"],
            "target": row["file_title"],
            "position": position,
        })
    return {"records": list(records.values())}
