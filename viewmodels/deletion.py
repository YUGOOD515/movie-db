"""Present the actual content affected by a checked whole-work deletion."""

from collections import defaultdict
from pathlib import PurePosixPath

from viewmodels.cards import build_progress, format_year
from viewmodels.title import format_ratings


def build_deletion_viewmodel(plan):
    title = plan["title"]
    records = plan["records"]
    titles = {row["title_id"]: row for row in records["title_basic"]}
    relations = {row["episode_id"]: row for row in records["title_relation"]}
    assets = plan["assets"]
    work_kind = "整部剧集" if title["title_type"] == "series" else "电影"

    def position(title_id):
        relation = relations.get(title_id)
        if not relation:
            return ""
        season, episode = relation["season_number"], relation["episode_number"]
        return f"S{season if season is not None else '?'} E{episode if episode is not None else '?'}"

    seasons = defaultdict(list)
    for relation in sorted(relations.values(), key=lambda row: (
        row["season_number"] if row["season_number"] is not None else -1,
        row["episode_number"] if row["episode_number"] is not None else -1, row["episode_id"]
    )):
        episode = titles[relation["episode_id"]]
        seasons[relation["season_number"]].append({
            "name": episode["display_title"], "position": position(episode["title_id"]),
            "rating": episode["average_rating"], "overview": episode.get("overview"),
        })
    images, seen = defaultdict(list), set()
    image_kinds = {"poster": "posters", "landscape_poster": "backdrops", "logo": "logos", "still": "stills"}
    image_names = {"poster": "海报", "landscape_poster": "背景图", "logo": "LOGO", "still": "剧照"}
    for row in records["title_artwork"]:
        path = row["local_path"]
        asset = assets.get(path, {})
        if path in seen or not asset.get("exists"):
            continue
        seen.add(path)
        kind = image_kinds.get(row["artwork_type"], "other")
        images[kind].append({"image": path, "name": image_names.get(row["artwork_type"], "图片"),
                             "position": position(row["title_id"]), "status": asset["status"], "uses": asset["uses"],
                             "is_wide": kind != "posters"})
    for row in plan["extra_images"]:
        if row["local_path"] not in seen:
            path = PurePosixPath(row["local_path"].replace("\\", "/").lower())
            kind = next((key for key in ("posters", "backdrops", "logos", "stills") if key in path.parts),
                        "stills" if "-still." in path.name else "other")
            images[kind].append({
                "image": row["local_path"], "name": {"posters": "海报", "backdrops": "背景图", "logos": "LOGO", "stills": "剧照"}.get(kind, "图片"),
                "position": "", "status": row["status"], "uses": row["uses"], "is_wide": kind != "posters",
            })
    cast, retained_cast, seen_people = [], [], set()
    for row in sorted(records["title_cast"], key=lambda row: (row["cast_order"], row["tmdb_person_id"])):
        path = row.get("local_path")
        asset = assets.get(path, {})
        key = (row["tmdb_person_id"], path)
        if key in seen_people or not asset.get("exists"):
            continue
        seen_people.add(key)
        person = {"image": path, "name": row["name"], "character": row["character_name"],
                  "status": asset["status"], "uses": asset["uses"]}
        if asset["status"] == "delete":
            cast.append(person)
        else:
            retained_cast.append(person)
    image_groups = [
        {"key": "posters", "name": "海报", "icon": "bi-image", "images": images["posters"], "is_cast": False},
        {"key": "backdrops", "name": "背景图", "icon": "bi-image", "images": images["backdrops"], "is_cast": False},
        {"key": "logos", "name": "LOGO", "icon": "bi-type", "images": images["logos"], "is_cast": False},
        {"key": "other", "name": "其他图片", "icon": "bi-images", "images": images["other"], "is_cast": False},
        {"key": "stills", "name": "剧照", "icon": "bi-image", "images": images["stills"], "is_cast": False},
        {"key": "cast", "name": "演员照片", "icon": "bi-person", "images": cast, "is_cast": True, "retained": False},
        {"key": "retained-cast", "name": "保留的演员照片", "icon": "bi-person-check", "images": retained_cast, "is_cast": True, "retained": True},
    ]
    files = {row["file_id"]: row for row in records["media_file"]}
    history = []
    for row in sorted(records["playback_progress"], key=lambda row: (row["watched_at"], row["playback_id"]), reverse=True):
        source = files[row["file_id"]]
        watched_title = titles[source["title_id"]]
        watch_name = watched_title["display_title"]
        history.append({
            "name": watch_name, "name_preview": watch_name[:7], "name_fades": len(watch_name) > 7,
            "position": position(source["title_id"]),
            "watched_at": row["watched_at"],
            "progress": build_progress(row["position_ms"], source.get("duration_ms")),
            "file_name": source["alist_path"].replace("\\", "/").rsplit("/", 1)[-1],
        })
    poster = next((row["local_path"] for row in records["title_artwork"]
                   if row["title_id"] == title["title_id"] and row["artwork_type"] == "poster"
                   and assets.get(row["local_path"], {}).get("exists")), None)
    return {
        "title_id": title["title_id"], "name": title["display_title"], "token": plan["token"],
        "confirm_label": f"删除{work_kind}", "year": format_year(title), "overview": title.get("overview"),
        "genres": (title.get("genres") or "").replace(",", " / "), "ratings": format_ratings(title),
        "poster": poster, "image_groups": [group for group in image_groups if group["images"]],
        "history": history, "collections": plan["collections"],
        "seasons": [{"name": "季号未知" if number is None else "特别篇" if number == 0 else f"第 {number} 季",
                     "episodes": episodes} for number, episodes in seasons.items()],
    }
