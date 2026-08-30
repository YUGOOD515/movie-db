import os
from pathlib import Path

from flask import abort, Flask, flash, redirect, render_template, request, url_for
from flask_session import Session

import helpers
import models
from db import get_db, init_app as init_database
from queries.catalog import get_catalog_options, query_catalog
from queries.collection import get_collection, get_collection_items
from queries.home import (
    get_collections,
    get_recently_added,
    get_top_rated_movies,
    get_unwatched,
    query_home,
)
from queries.manager import query_manager
from queries.title import (
    get_playable_file,
    get_title_id_by_tmdb,
    query_title,
)
from viewmodels.catalog import (
    build_catalog_viewmodel,
    build_library_viewmodel,
    parse_catalog_filters,
)
from viewmodels.collection import build_collection_viewmodel
from viewmodels.home import build_home_viewmodel
from viewmodels.manager import build_manager_viewmodel
from viewmodels.title import build_title_viewmodel


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "library.db"

app = Flask(__name__)
app.config.update(
    DATABASE=DATABASE_PATH,
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY", "moviedb-local-development"),
    SESSION_PERMANENT=False,
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=str(BASE_DIR / ".flask_session"),
)
Session(app)
init_database(app)


@app.get("/")
def index():
    raw = query_home(get_db())
    page = build_home_viewmodel(raw)
    return render_template("index.html", page=page)


@app.get("/recent")
@app.get("/recent-added")
def recent_added_library():
    raw = get_recently_added(get_db(), limit=None)
    page = build_library_viewmodel(
        raw,
        title="最近添加",
        description="按入库时间由近到远排列",
        empty_message="媒体库暂时为空。",
    )
    return render_template("library.html", page=page)


@app.get("/unwatched")
def unwatched_library():
    raw = get_unwatched(get_db(), limit=None)
    page = build_library_viewmodel(
        raw,
        title="未观看",
        description="尚无播放记录的电影和剧集",
        empty_message="目前没有未观看的媒体。",
    )
    return render_template("library.html", page=page)


@app.get("/top")
@app.get("/top-rated")
def top_rated_library():
    raw = get_top_rated_movies(get_db(), limit=9)
    page = build_library_viewmodel(
        raw,
        title="最受好评的电影",
        description="按评分由高到低排列，并排除票数过少的样本",
        empty_message="当前没有满足票数条件的电影。",
        show_rating=True,
    )
    return render_template("top.html", page=page)


@app.get("/catalog")
def catalog():
    filters = parse_catalog_filters(request.args)
    raw = query_catalog(get_db(), filters)
    options = get_catalog_options(get_db())
    page = build_catalog_viewmodel(raw, options, filters)
    return render_template("genre_page.html", page=page)


@app.get("/genres")
def movie_genres_library():
    values = request.args.to_dict()
    if values.get("media_type") == "tv":
        values["type"] = "series"
    elif values.get("media_type") == "movie":
        values["type"] = "movie"
    values.pop("media_type", None)
    return redirect(url_for("catalog", **values), code=308)


@app.get("/collections")
@app.get("/favorites")
def favorites_library():
    collections = get_collections(get_db())
    page = build_collection_viewmodel(
        None,
        get_collection_items(get_db()),
        collections,
    )
    return render_template("favorites_page.html", page=page)


@app.get("/collection/<int:collection_id>")
def collection_detail(collection_id: int):
    collection = get_collection(get_db(), collection_id)
    if collection is None:
        abort(404)
    raw = get_collection_items(get_db(), collection_id)
    page = build_collection_viewmodel(
        collection,
        raw,
        get_collections(get_db()),
    )
    return render_template("favorites_page.html", page=page)


@app.post("/collection/<int:collection_id>/<title_id>/toggle")
def toggle_collection_item(collection_id: int, title_id: str):
    try:
        added = models.toggle_library_collection_item(
            app.config["DATABASE"], collection_id, title_id
        )
    except ValueError:
        abort(404)
    flash("已加入收藏。" if added else "已取消收藏。", "success")
    return redirect(_safe_return_url())


@app.get("/title/<title_id>")
def title_detail(title_id: str):
    raw = query_title(
        get_db(),
        title_id,
        season_number=request.args.get("season", type=int),
        episode_number=request.args.get("episode", type=int),
    )
    if raw is None:
        abort(404)
    media = build_title_viewmodel(raw)
    return render_template("detail.html", media=media)


@app.get("/movies/<int:tmdb_id>")
def movie_detail(tmdb_id: int):
    title_id = get_title_id_by_tmdb(get_db(), "movie", tmdb_id)
    if title_id is None:
        abort(404)
    return redirect(url_for("title_detail", title_id=title_id), code=308)


@app.get("/series/<int:tmdb_id>")
def tv_detail(tmdb_id: int):
    title_id = get_title_id_by_tmdb(get_db(), "series", tmdb_id)
    if title_id is None:
        abort(404)
    return redirect(url_for("title_detail", title_id=title_id), code=308)


@app.get("/manager")
@app.get("/media")
def media():
    title_type = request.args.get("type", "all")
    if title_type == "tv":
        title_type = "series"
    if title_type not in ("all", "movie", "series"):
        title_type = "all"
    raw = query_manager(
        get_db(),
        query=(request.args.get("q") or "").strip() or None,
        title_type=title_type,
    )
    page = build_manager_viewmodel(raw)
    page["active_tab"] = (
        "associate" if request.args.get("tab") == "associate" else "overview"
    )
    return render_template(
        "media.html",
        active_tab=page["active_tab"],
        overview=page,
        alist_origin=os.environ.get("ALIST_ORIGIN", ""),
    )


@app.post("/manager/<title_id>/artwork")
def select_media_artwork(title_id: str):
    artwork_type = request.form.get("artwork_type", "")
    position = request.form.get("position", type=int)
    selections = []
    if artwork_type and position is not None:
        selections.append((artwork_type, position))
    else:
        for field, mapped_type in (
            ("poster_position", "poster"),
            ("backdrop_position", "landscape_poster"),
            ("logo_position", "logo"),
        ):
            selected_position = request.form.get(field, type=int)
            if selected_position is not None:
                selections.append((mapped_type, selected_position))
    if not selections:
        abort(400)
    try:
        for selected_type, selected_position in selections:
            models.set_library_primary_artwork(
                app.config["DATABASE"],
                title_id,
                selected_type,
                selected_position,
            )
    except ValueError:
        abort(404)
    flash("主图片已更新。", "success")
    return redirect(_safe_return_url(default_endpoint="media"))


@app.post("/play/<int:file_id>")
def play_media_file(file_id: int):
    source = get_playable_file(get_db(), file_id)
    if source is None:
        abort(404)

    progress = max(int(source.get("position_ms") or 0), 0)
    duration = max(int(source.get("duration_ms") or 0), 0)
    start_position = 0 if duration and progress / duration >= 0.95 else progress
    database_path = app.config["DATABASE"]
    playback_session = {"playback_id": None}

    def save_progress(position_ms: int, _duration_ms: int):
        playback_session["playback_id"] = models.save_library_playback_progress(
            database_path,
            file_id,
            position_ms,
            playback_id=playback_session["playback_id"],
        )

    video_url = helpers.get_video_link(source["alist_path"])
    helpers.play_video(
        video_url,
        start_position_ms=start_position,
        progress_callback=save_progress,
    )
    return "", 204


def _safe_return_url(default_endpoint: str = "title_detail") -> str:
    return_to = request.form.get("return_to", "")
    if return_to.startswith("/") and not return_to.startswith("//"):
        return return_to
    if default_endpoint == "media":
        return url_for("media")
    title_id = request.view_args.get("title_id") if request.view_args else None
    return url_for("title_detail", title_id=title_id) if title_id else url_for("index")


if __name__ == "__main__":
    app.run(debug=True)
