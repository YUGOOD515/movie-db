import os
import re
from pathlib import Path

from flask import abort, Flask, flash, redirect, render_template, request, url_for
from flask_session import Session

import helpers
import models


def extract_resolution(path_or_name: str | None) -> str:
    if not path_or_name:
        return "1080P"
    text = str(path_or_name).upper()
    if re.search(r"\b(4K|2160P|UHD)\b", text) or "4K" in text or "2160P" in text:
        return "4K"
    if re.search(r"\b(2K|1440P|QHD)\b", text) or "2K" in text or "1440P" in text:
        return "2K"
    if re.search(r"\b(1080P|1080I|FHD)\b", text) or "1080P" in text or "1080I" in text:
        return "1080P"
    if re.search(r"\b(720P|HD)\b", text) or "720P" in text:
        return "720P"
    if re.search(r"\b(480P|SD)\b", text) or "480P" in text:
        return "480P"
    if re.search(r"\b(360P)\b", text) or "360P" in text:
        return "360P"
    return "1080P"


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "movie.db"
IMAGE_ROOT = BASE_DIR / "static" / "images"
GENRE_ICONS = {
    "Sci-Fi & Fantasy": "stars",
    "冒险": "compass",
    "剧情": "masks-theater",
    "动作": "lightning-charge",
    "动作冒险": "lightning-charge",
    "动画": "palette",
    "喜剧": "emoji-laughing",
    "奇幻": "stars",
    "家庭": "people",
    "恐怖": "eye",
    "悬疑": "question-diamond",
    "爱情": "heart",
    "犯罪": "fingerprint",
    "科幻": "rocket-takeoff",
}

app = Flask(__name__)
app.config["DATABASE"] = DATABASE_PATH
app.config["IMAGE_ROOT"] = IMAGE_ROOT
app.config["SECRET_KEY"] = os.environ.get(
    "FLASK_SECRET_KEY",
    "moviedb-local-development",
)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)


def get_media_mappings():
    """Return stored movie and episode relationships."""
    return (
        models.get_movie_mappings(app.config["DATABASE"]),
        models.get_tv_episode_mappings(app.config["DATABASE"]),
    )


def add_media_routes(item: dict):
    """Add route and playback URLs without mixing them into model queries."""
    if item["media_type"] == "movie":
        item["detail_url"] = url_for(
            "movie_detail",
            movie_id=item["media_id"],
        )
    else:
        item["detail_url"] = url_for(
            "tv_detail",
            series_id=item["media_id"],
        )

    source_id = item.get("source_order")
    item["play_url"] = (
        url_for(
            "play_media_source",
            media_type=item["media_type"],
            source_id=source_id,
        )
        if source_id is not None
        else None
    )

    return item


def add_genre_presentation(genre: dict, media_type: str):
    genre["icon"] = GENRE_ICONS.get(genre["genre_name"], "tag")
    genre["detail_url"] = url_for(
        "movie_genres_library",
        media_type=media_type,
        genre_id=genre["genre_id"],
    )

    return genre


def prepare_homepage_data():
    homepage = models.get_homepage_data(app.config["DATABASE"])
    for section_name in (
        "continue_watching",
        "recent_added",
        "unwatched",
        "top_rated_movies",
    ):
        for item in homepage[section_name]:
            add_media_routes(item)

    for section_name, media_type in (
        ("genre_features", "movie"),
        ("tv_genre_features", "tv"),
    ):
        for genre in homepage[section_name]:
            add_genre_presentation(genre, media_type)

    for folder in homepage["favorite_folders"]:
        folder["detail_url"] = url_for(
            "favorites_library",
            folder_id=folder["folder_id"],
        )

    return homepage


def prepare_library_page(section_name: str):
    items = models.get_library_page_items(
        app.config["DATABASE"],
        section_name,
    )
    for item in items:
        add_media_routes(item)

    return items


def prepare_favorite_page(folder_id: int | None):
    page = models.get_favorite_page_data(
        app.config["DATABASE"],
        folder_id,
    )
    if page is None:
        abort(404)
    for item in page["items"]:
        add_media_routes(item)

    return page


def prepare_genre_page(filters: dict):
    items = models.get_filtered_library_items(
        app.config["DATABASE"],
        **filters,
    )
    for item in items:
        add_media_routes(item)

    options = models.get_library_filter_options(
        app.config["DATABASE"],
        filters["media_type"],
    )
    for genre in options["genres"]:
        genre["icon"] = GENRE_ICONS.get(genre["genre_name"], "tag")

    return items, options


def import_movie(movie_name: str, alist_path: str):
    details = helpers.find_media("movie", movie_name)
    movie_id = int(details["id"])
    images = helpers.download_media_images(
        "movie",
        movie_id,
        helpers.search_media_images("movie", movie_id),
        app.config["IMAGE_ROOT"],
    )
    models.save_movie(
        app.config["DATABASE"],
        details,
        images,
        alist_path,
    )

    return f"已关联电影《{details.get('title') or movie_name}》与 AList 文件。"


def import_tv_episode(
    series_name: str,
    season_number: int,
    episode_number: int,
    alist_path: str,
):
    series_details = helpers.find_media("tv", series_name)
    series_id = int(series_details["id"])
    season_details = helpers.search_tv_season(series_id, season_number)
    if int(season_details.get("season_number", -1)) != season_number:
        raise ValueError("TMDB 季数据返回异常。")

    episode = helpers.get_tv_episode(season_details, episode_number)
    series_images = None
    if not models.get_tv_series_images(app.config["DATABASE"], series_id):
        series_images = helpers.download_media_images(
            "tv",
            series_id,
            helpers.search_media_images("tv", series_id),
            app.config["IMAGE_ROOT"],
        )
    episode_still = helpers.download_tv_episode_still(
        series_id,
        season_number,
        episode,
        app.config["IMAGE_ROOT"],
    )
    models.save_tv_episode(
        app.config["DATABASE"],
        series_details,
        season_details,
        episode_number,
        alist_path,
        series_images,
        episode_still,
    )

    display_name = series_details.get("name") or series_name
    return (
        f"已关联剧集《{display_name}》S{season_number:02d}E{episode_number:02d}"
        f"《{episode.get('name') or '未命名'}》与 AList 文件。"
    )


def process_media_submission():
    media_type = request.form.get("media_type", "movie")
    media_name = (
        request.form.get("media_name")
        or request.form.get("movie_name")
        or ""
    ).strip()
    alist_link = request.form.get("alist_url", "").strip()

    if media_type not in ("movie", "tv"):
        raise ValueError("媒体类型无效。")
    if not media_name or not alist_link:
        raise ValueError("请同时填写媒体名称和 AList 视频直链。")

    alist_path = helpers.parse_alist_url(alist_link)
    helpers.validate_alist_video_url(alist_link)
    if media_type == "movie":
        return import_movie(media_name, alist_path)

    season_number, episode_number = helpers.parse_tv_position(
        request.form.get("season_number"),
        request.form.get("episode_number"),
    )
    return import_tv_episode(
        media_name,
        season_number,
        episode_number,
        alist_path,
    )


@app.route("/")
def index():
    return render_template("index.html", **prepare_homepage_data())


@app.route("/recent-added")
def recent_added_library():
    return render_template(
        "library_page.html",
        page_title="最近添加",
        page_description="按入库时间由近到远排列",
        empty_message="媒体库暂时为空，可先关联电影或剧集。",
        items=prepare_library_page("recent_added"),
    )


@app.route("/unwatched")
def unwatched_library():
    return render_template(
        "library_page.html",
        page_title="未观看",
        page_description="尚未开始播放的电影和剧集",
        empty_message="目前没有未观看的媒体。",
        items=prepare_library_page("unwatched"),
    )


@app.route("/favorites")
def favorites_library():
    return render_template(
        "favorites_page.html",
        **prepare_favorite_page(request.args.get("folder_id", type=int)),
    )


@app.post("/favorites/<media_type>/<int:media_id>/toggle")
def toggle_favorite(media_type: str, media_id: int):
    if media_type not in ("movie", "tv"):
        abort(404)
    try:
        models.toggle_media_favorite(
            app.config["DATABASE"],
            media_type,
            media_id,
        )
    except ValueError:
        abort(404)

    return redirect(
        url_for(
            "movie_detail" if media_type == "movie" else "tv_detail",
            **({"movie_id": media_id} if media_type == "movie" else {"series_id": media_id}),
        )
    )


@app.route("/genres")
def movie_genres_library():
    media_type = request.args.get("media_type", "all")
    if media_type not in ("all", "movie", "tv"):
        media_type = "all"
    min_rating = request.args.get("min_rating", type=float)
    if min_rating is not None and not 0 <= min_rating <= 10:
        min_rating = None
    filters = {
        "media_type": media_type,
        "genre_id": request.args.get("genre_id", type=int),
        "country_code": request.args.get("country_code") or None,
        "language_code": request.args.get("language_code") or None,
        "year": request.args.get("year") or None,
        "min_rating": min_rating,
    }
    items, filter_options = prepare_genre_page(filters)
    return render_template(
        "genre_page.html",
        items=items,
        filter_options=filter_options,
        filters=filters,
    )


@app.route("/top-rated")
def top_rated_library():
    return render_template(
        "top_rated_page.html",
        items=prepare_library_page("top_rated_movies"),
    )


@app.route("/play/<media_type>/<int:source_id>")
def play_media_source(media_type: str, source_id: int):
    if media_type not in ("movie", "tv"):
        abort(404)

    source = models.get_media_source(
        app.config["DATABASE"],
        media_type,
        source_id,
    )
    if source is None:
        abort(404)

    progress = models.get_watch_progress(
        app.config["DATABASE"],
        media_type,
        source["media_id"],
    )
    video_url = helpers.get_video_link(source["alist_path"])
    database_path = app.config["DATABASE"]

    def save_progress(position_ms: int, duration_ms: int):
        models.save_watch_progress(
            database_path,
            media_type,
            source["media_id"],
            position_ms,
            duration_ms,
        )

    helpers.play_video(
        video_url,
        start_position_ms=progress["resume_position_ms"],
        progress_callback=save_progress,
    )

    return "", 204


def render_media_detail(media_type: str, media_id: int):
    detail = models.get_media_detail(
        app.config["DATABASE"],
        media_type,
        media_id,
    )
    if detail is None:
        abort(404)

    detail["is_favorite"] = models.is_media_favorite(
        app.config["DATABASE"],
        media_type,
        media_id,
    )
    add_media_routes(detail)
    for source in detail["sources"]:
        source["play_url"] = url_for(
            "play_media_source",
            media_type=media_type,
            source_id=source["source_id"],
        )
    watched_sources = [
        source
        for source in detail["sources"]
        if source.get("position_ms", 0) > 0 or source.get("updated_at")
    ]
    if watched_sources:
        latest_watched = max(
            watched_sources,
            key=lambda source: (
                source.get("updated_at") or "",
                source.get("source_id", 0),
            ),
        )
        if latest_watched.get("is_completed", False):
            if media_type == "tv":
                try:
                    latest_idx = detail["sources"].index(latest_watched)
                    if latest_idx + 1 < len(detail["sources"]):
                        detail["featured_source"] = detail["sources"][latest_idx + 1]
                    else:
                        detail["featured_source"] = detail["sources"][0]
                except (ValueError, IndexError):
                    detail["featured_source"] = (
                        detail["sources"][0] if detail["sources"] else None
                    )
            else:
                detail["featured_source"] = latest_watched
        else:
            detail["featured_source"] = latest_watched
    else:
        detail["featured_source"] = (
            detail["sources"][0] if detail["sources"] else None
        )

    if detail["featured_source"]:
        detail["play_url"] = detail["featured_source"]["play_url"]
        is_completed = detail["featured_source"].get("is_completed", False)
        if not is_completed and detail["featured_source"].get("resume_position_ms", 0) > 0:
            detail["progress"] = detail["featured_source"].get("progress", 0)
            detail["position_label"] = detail["featured_source"].get("position_label", "")
        else:
            detail["progress"] = 0
            detail["position_label"] = ""
    else:
        detail["progress"] = 0
        detail["position_label"] = ""

    detail["featured_episode"] = None
    for season in detail["seasons"]:
        for episode in season["episodes"]:
            episode["featured_source"] = (
                next(
                    (
                        source
                        for source in episode["sources"]
                        if detail["featured_source"]
                        and source["source_id"]
                        == detail["featured_source"]["source_id"]
                    ),
                    None,
                )
                or (episode["sources"][0] if episode["sources"] else None)
            )
            episode["play_url"] = (
                episode["featured_source"]["play_url"]
                if episode["featured_source"]
                else None
            )
            if (
                detail["featured_source"]
                and episode["episode_id"]
                == detail["featured_source"].get("episode_id")
            ):
                detail["featured_episode"] = episode

    detail["vote_count_label"] = f'{detail["vote_count"]:,}'
    source_path = ""
    if detail.get("featured_source") and detail["featured_source"].get("alist_path"):
        source_path = detail["featured_source"]["alist_path"]
    elif detail.get("sources") and detail["sources"] and detail["sources"][0].get("alist_path"):
        source_path = detail["sources"][0]["alist_path"]
    detail["resolution"] = extract_resolution(source_path)
    detail["date_label"] = detail.get("release_date") or detail.get("year") or ""

    if media_type == "tv":
        runtime = None
        if detail.get("featured_episode") and detail["featured_episode"].get("runtime"):
            runtime = detail["featured_episode"]["runtime"]
        elif detail.get("runtime"):
            runtime = detail["runtime"]
        detail["runtime_label"] = f"{runtime} 分钟" if runtime else ""

        current_season = next(
            (
                s for s in detail.get("seasons", [])
                if detail.get("featured_source") and s.get("season_number") == detail["featured_source"].get("season_number")
            ),
            detail["seasons"][0] if detail.get("seasons") else None,
        )
        if current_season and current_season.get("vote_average") is not None and current_season["vote_average"] > 0:
            detail["season_vote_average"] = current_season["vote_average"]
        else:
            detail["season_vote_average"] = detail.get("vote_average")

        status_labels = {
            "Ended": "已完结",
            "Canceled": "已取消",
            "Returning Series": "连载中",
        }
        detail["status_label"] = (
            "连载中"
            if detail["in_production"]
            else status_labels.get(
                detail["media_status"],
                detail["media_status"],
            )
        )
    else:
        detail["runtime_label"] = f"{detail['runtime']} 分钟" if detail.get("runtime") else ""
        detail["season_vote_average"] = detail.get("vote_average")
        detail["status_label"] = {
            "Released": "已上映",
            "Post Production": "后期制作",
            "In Production": "制作中",
            "Planned": "计划中",
            "Canceled": "已取消",
        }.get(detail["media_status"], detail["media_status"])

    detail["external_links"] = [
        {
            "label": "TMDB",
            "url": (
                f"https://www.themoviedb.org/movie/{media_id}"
                if media_type == "movie"
                else f"https://www.themoviedb.org/tv/{media_id}"
            ),
        }
    ]
    if detail.get("imdb_id"):
        detail["external_links"].append(
            {
                "label": "IMDb",
                "url": f'https://www.imdb.com/title/{detail["imdb_id"]}/',
            }
        )
    if detail.get("homepage"):
        detail["external_links"].append(
            {"label": "官网", "url": detail["homepage"]}
        )

    return render_template("detail.html", media=detail)


@app.route("/movies/<int:movie_id>")
def movie_detail(movie_id: int):
    return render_media_detail("movie", movie_id)


@app.route("/series/<int:series_id>")
def tv_detail(series_id: int):
    return render_media_detail("tv", series_id)


@app.route("/media", methods=["GET", "POST"])
def media():
    if request.method == "POST":
        try:
            flash(process_media_submission(), "success")
        except ValueError as error:
            flash(str(error), "danger")
        except Exception:
            app.logger.exception("Importing media failed")
            flash("查询或保存失败，请检查 TMDB、AList 和数据库状态。", "danger")

        return redirect(url_for("media", tab="associate"))

    tab = request.args.get("tab", "overview")
    query = request.args.get("q", "").strip()
    media_filter = request.args.get("type", "all")
    if media_filter not in ("all", "movie", "tv"):
        media_filter = "all"

    overview_data = models.get_media_overview_data(
        app.config["DATABASE"],
        query=query or None,
        media_type=media_filter,
    )
    for item in overview_data["media_items"]:
        add_media_routes(item)

    return render_template(
        "media.html",
        active_tab=tab,
        overview=overview_data,
        alist_origin=helpers.alist_url,
    )


if __name__ == "__main__":
    app.run(debug=True)
