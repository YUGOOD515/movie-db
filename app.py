import os
from pathlib import Path

from flask import abort, Flask, flash, redirect, render_template, request, url_for
from flask_session import Session

import helpers
import models


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "movie.db"
IMAGE_ROOT = BASE_DIR / "static" / "images"

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
    """Add browser URLs to stored movie and episode relationships."""
    movie_mappings = models.get_movie_mappings(app.config["DATABASE"])
    episode_mappings = models.get_tv_episode_mappings(app.config["DATABASE"])
    for mapping in movie_mappings + episode_mappings:
        mapping["alist_url"] = helpers.build_alist_url(mapping["alist_path"])

    return movie_mappings, episode_mappings


def add_media_urls(item: dict):
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

    alist_path = item.get("alist_path")
    item["play_url"] = (
        helpers.build_alist_url(alist_path)
        if alist_path
        else None
    )

    return item


def prepare_homepage_data():
    homepage = models.get_homepage_data(app.config["DATABASE"])
    for section_name in (
        "continue_watching",
        "recent_added",
        "unwatched",
        "top_rated_movies",
    ):
        for item in homepage[section_name]:
            add_media_urls(item)

    for genre in homepage["genre_features"]:
        genre["detail_url"] = url_for(
            "movie_detail",
            movie_id=genre["media_id"],
        )

    return homepage


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


def render_media_detail(media_type: str, media_id: int):
    detail = models.get_media_detail(
        app.config["DATABASE"],
        media_type,
        media_id,
    )
    if detail is None:
        abort(404)

    add_media_urls(detail)
    for source in detail["sources"]:
        source["alist_url"] = helpers.build_alist_url(source["alist_path"])
    if detail["sources"]:
        detail["play_url"] = detail["sources"][-1]["alist_url"]
    detail["featured_source"] = (
        detail["sources"][-1]
        if detail["sources"]
        else None
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

        return redirect(url_for("media"))

    movie_mappings, episode_mappings = get_media_mappings()
    return render_template(
        "media.html",
        movie_mappings=movie_mappings,
        episode_mappings=episode_mappings,
        alist_origin=helpers.alist_url,
    )


if __name__ == "__main__":
    app.run(debug=True)
