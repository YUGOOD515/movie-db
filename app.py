import importlib
import json
import os
import secrets
import sqlite3
from pathlib import Path

from flask import abort, Flask, flash, g, jsonify, redirect, render_template, request, url_for

from models.database import get_db, init_app as init_database
from models.library import (
    save_library_playback_progress,
    set_library_primary_artworks,
    toggle_library_collection_item,
)
from queries.catalog import get_catalog_options, query_catalog
from queries.collection import query_collection
from queries.home import (
    get_recently_added,
    get_top_rated_movies,
    get_unwatched,
    query_home,
)
from queries.manager import get_rating_title_ids, query_manager
from queries.title import (
    get_playable_file,
    get_title_id_by_tmdb,
    query_title,
)
from services.playback import is_effectively_completed
from services.image_tones import load_tones
from services.thumbnails import get_card_image_path
from viewmodels.catalog import (
    build_catalog_viewmodel,
    build_library_viewmodel,
    parse_catalog_filters,
)
from viewmodels.collection import build_collection_viewmodel
from viewmodels.home import build_home_viewmodel
from viewmodels.manager import build_manager_viewmodel
from viewmodels.title import build_title_viewmodel

# 外部接口与写入服务（requests、Pillow、GuessIt 等）在首次使用时才导入，
# 使只读快照与云端渲染只依赖 Flask。


class _LazyModule:
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attribute: str):
        return getattr(importlib.import_module(self._name), attribute)


def _lazy(module: str, name: str):
    def call(*args, **kwargs):
        return getattr(importlib.import_module(module), name)(*args, **kwargs)

    call.__name__ = name
    return call


requests = _LazyModule("requests")
alist = _LazyModule("connectors.alist")
player = _LazyModule("connectors.player")
tmdb = _LazyModule("connectors.tmdb")
_media_import = _LazyModule("services.media_import")
_title_deletion = _LazyModule("services.title_deletion")

import_media_batch_service = _lazy("services.media_import", "import_media_batch")
import_media_service = _lazy("services.media_import", "import_media")
normalize_batch_import_items = _lazy("services.media_import", "normalize_batch_import_items")
get_rating_refresh_status = _lazy("services.rating_sync", "get_rating_refresh_status")
start_rating_refresh = _lazy("services.rating_sync", "start_rating_refresh")
search_titles = _lazy("services.title_search", "search_titles")
select_title = _lazy("services.title_search", "select_title")
start_scan = _lazy("services.media_discovery", "start_scan")
scan_status = _lazy("services.media_discovery", "scan_status")
title_metadata = _lazy("services.media_association", "title_metadata")
check_association = _lazy("services.media_association", "check_association")
complete_association = _lazy("services.media_association", "complete_association")
preview_title_deletion = _lazy("services.title_deletion", "preview_title_deletion")
execute_title_deletion = _lazy("services.title_deletion", "execute_title_deletion")
build_search_viewmodel = _lazy("viewmodels.title_search", "build_search_viewmodel")
build_selection_viewmodel = _lazy("viewmodels.title_search", "build_selection_viewmodel")
build_deletion_viewmodel = _lazy("viewmodels.deletion", "build_deletion_viewmodel")


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("MOVIEDB_DATABASE") or BASE_DIR / "library.db")
IMAGE_ROOT = BASE_DIR / "static" / "images"
MAX_BATCH_JSON_BYTES = 1024 * 1024
READ_ONLY = os.environ.get("MOVIEDB_READ_ONLY", "").strip() == "1"
READ_ONLY_MESSAGE = "本地 MovieDB 未连接，当前为只读快照。"
# 只读模式下仍可访问的 GET 端点；其余端点会调用外部接口或修改数据。
READ_ONLY_ENDPOINTS = {
    "static",
    "index",
    "recent_added_library",
    "unwatched_library",
    "top_rated_library",
    "catalog",
    "movie_genres_library",
    "favorites_library",
    "collection_detail",
    "title_detail",
    "episode_panel",
    "movie_detail",
    "tv_detail",
    "media",
}

app = Flask(__name__)
app.config.update(
    DATABASE=DATABASE_PATH,
    IMAGE_ROOT=IMAGE_ROOT,
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32),
    READ_ONLY=READ_ONLY,
    # 只读快照的元数据：同步时间、AList 地址与已生成的缩略图清单。
    SNAPSHOT_AT=os.environ.get("MOVIEDB_SNAPSHOT_AT", ""),
    ALIST_ORIGIN=os.environ.get("ALIST_ORIGIN", "http://127.0.0.1:5244").rstrip("/"),
    THUMBNAIL_INDEX=None,
    # 只读快照的影像色调表 {local_path: [color, blurhash]}；本地模式读取 static/thumbnails/tones.json。
    IMAGE_TONES=None,
)
init_database(app)
if not READ_ONLY:
    from services.alist_process import start_configured_alist
    from services import cloud_sync

    start_configured_alist()
    cloud_sync.init_app(app)
    if not os.environ.get("MOVIEDB_TONES_DISABLED"):
        from services.image_tones import start_backfill

        start_backfill(DATABASE_PATH, app.static_folder)


@app.context_processor
def inject_read_only():
    return {
        "read_only": app.config["READ_ONLY"],
        "snapshot_at": app.config["SNAPSHOT_AT"],
    }


@app.before_request
def guard_read_only():
    if not app.config["READ_ONLY"]:
        return None
    if request.method in ("GET", "HEAD") and request.endpoint in READ_ONLY_ENDPOINTS:
        return None
    if request.endpoint is None:
        return None
    if request.accept_mimetypes.accept_html and not request.accept_mimetypes.accept_json:
        return render_template("read_only.html"), 503
    return jsonify(error=READ_ONLY_MESSAGE, read_only=True), 503


@app.after_request
def mark_origin(response):
    # 云端 Worker 根据该标记区分本地应用响应与 Tunnel 离线错误。
    response.headers["X-MovieDB-Origin"] = "snapshot" if app.config["READ_ONLY"] else "local"
    return response


@app.template_filter("card_image")
def card_image(path: str | None) -> str | None:
    cache = g.setdefault("card_image_paths", {})
    if path not in cache:
        options = {"static_root": app.static_folder or BASE_DIR / "static"}
        if app.config["THUMBNAIL_INDEX"] is not None:
            options["thumbnail_index"] = app.config["THUMBNAIL_INDEX"]
        cache[path] = get_card_image_path(path, **options)
    return cache[path]


@app.template_filter("image_tone")
def image_tone(path: str | None) -> dict | None:
    """海报 / 背景图的环境色与 BlurHash；尚未生成时返回 None。"""
    if not path:
        return None
    if app.config["IMAGE_TONES"] is not None:
        value = app.config["IMAGE_TONES"].get(path)
        return {"color": value[0], "blurhash": value[1]} if value else None
    tones = g.get("image_tones")
    if tones is None:
        tones = g.image_tones = load_tones(app.static_folder or BASE_DIR / "static")
    return tones.get(path)


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
    raw = query_collection(get_db())
    page = build_collection_viewmodel(raw)
    return render_template("favorites_page.html", page=page)


@app.get("/collection/<int:collection_id>")
def collection_detail(collection_id: int):
    raw = query_collection(get_db(), collection_id)
    if raw is None:
        abort(404)
    page = build_collection_viewmodel(raw)
    return render_template("favorites_page.html", page=page)


@app.post("/collection/<int:collection_id>/<title_id>/toggle")
def toggle_collection_item(collection_id: int, title_id: str):
    try:
        added = toggle_library_collection_item(
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
    media = build_title_viewmodel(
        raw,
        return_to=request.full_path,
        select_resume=request.args.get("resume") == "1",
    )
    return render_template("detail.html", media=media)


@app.get("/title/<title_id>/episode-panel")
def episode_panel(title_id: str):
    raw = query_title(get_db(), title_id)
    if raw is None or raw["requested"]["title_type"] != "episode":
        abort(404)
    media = build_title_viewmodel(raw)
    return render_template(
        "episode_panel.html",
        episode=media["selected_episode"],
        genres=media["genres"],
    )


@app.post("/title/<title_id>/resume")
def resume_title(title_id: str):
    raw = query_title(get_db(), title_id)
    if raw is None or raw["root"]["title_type"] != "series":
        abort(404)
    media = build_title_viewmodel(raw, select_resume=True)
    episode = media.get("selected_episode")
    source = episode.get("featured_source") if episode else None
    if not source:
        abort(404)
    return redirect(
        url_for("play_media_file", file_id=source["file_id"]),
        code=307,
    )


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
    active_tab = (
        "associate" if request.args.get("tab") == "associate" else "overview"
    )
    page = build_manager_viewmodel(
        raw,
        active_tab=active_tab,
        return_to=request.full_path,
    )
    return render_template(
        "media.html",
        active_tab=active_tab,
        overview=page,
        alist_origin=app.config["ALIST_ORIGIN"],
    )


@app.get("/manager/titles/search")
def search_import_titles():
    media_type = request.args.get("type", "movie")
    try:
        payload = search_titles(
            request.args.get("q", ""), media_type, request.args.get("page", "1")
        )
    except requests.RequestException:
        return jsonify(error="TMDB 暂时无法访问，请稍后重试或手动填写 IMDb ID。"), 502
    except RuntimeError:
        return jsonify(error="TMDB 尚未配置，请手动填写 IMDb ID。"), 503
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(build_search_viewmodel(payload, media_type))


@app.get("/manager/titles/select")
def select_import_title():
    try:
        title = select_title(
            request.args.get("type", "movie"),
            request.args.get("tmdb_id", ""),
        )
    except requests.RequestException:
        return jsonify(error="无法获取 IMDb 映射，请稍后重试。"), 502
    except RuntimeError:
        return jsonify(error="TMDB 尚未配置，请手动填写 IMDb ID。"), 503
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(build_selection_viewmodel(title))


@app.get("/manager/titles/poster")
def import_title_poster():
    media_type = request.args.get("type", "")
    tmdb_id = request.args.get("tmdb_id", "")
    if media_type not in ("movie", "tv") or not tmdb_id.isdigit() or not 1 <= len(tmdb_id) <= 10:
        abort(400)
    try:
        path = tmdb.chinese_search_poster(media_type, int(tmdb_id))
    except (requests.RequestException, RuntimeError, ValueError):
        abort(502)
    if not path:
        abort(404)
    response = redirect(f"{tmdb.TMDB_IMAGE}/w185{path}")
    response.cache_control.max_age = 600
    return response


@app.route("/manager/discovery", methods=["GET", "POST"])
def discover_import_media():
    if request.method == "POST":
        start_scan(app.config["DATABASE"], refresh=request.args.get("refresh") == "1")
    return jsonify(scan_status(app.config["DATABASE"]))


@app.get("/manager/titles/episodes")
def import_title_metadata():
    try:
        return jsonify(title_metadata(app.config["DATABASE"], request.args.get("title_id", "")))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except requests.RequestException:
        return jsonify(error="TMDB 暂时无法访问，请稍后重试。"), 502
    except RuntimeError:
        return jsonify(error="TMDB 尚未配置。"), 503


@app.post("/manager/association/<action>")
def associate_discovered_media(action):
    if action not in ("check", "complete"):
        abort(404)
    if request.content_length and request.content_length > MAX_BATCH_JSON_BYTES:
        return jsonify(error="关联数据过大。"), 413
    payload = request.get_json(silent=True)
    try:
        arguments = dict(library_database=app.config["DATABASE"], payload=payload)
        if action == "complete":
            result = complete_association(**arguments, image_root=app.config["IMAGE_ROOT"])
        else:
            result = check_association(**arguments)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except (OSError, RuntimeError, requests.RequestException, sqlite3.Error):
        return jsonify(error="关联服务暂时不可用，请检查 AList 和数据库后重试。"), 503
    return jsonify(result), 200 if result["valid"] else 422


@app.post("/manager/import")
def import_media():
    try:
        result = import_media_service(
            library_database=app.config["DATABASE"],
            image_root=app.config["IMAGE_ROOT"],
            title_id=request.form.get("title_id", ""),
            season_number=request.form.get("season_number", ""),
            episode_number=request.form.get("episode_number", ""),
            alist_url=request.form.get("alist_url", ""),
        )
    except _media_import.MediaImportError as error:
        flash(str(error), "danger")
        return redirect(url_for("media", tab="associate"))

    flash(
        f"已录入 {result['display_title']}，关联媒体文件 #{result['file_id']}。",
        "success",
    )
    return redirect(
        url_for(
            "media",
            tab="overview",
            q=result["root_display_title"],
        )
    )


@app.post("/manager/import/batch")
def import_media_batch():
    try:
        items = _read_batch_import_items()
        result = import_media_batch_service(
            library_database=app.config["DATABASE"],
            image_root=app.config["IMAGE_ROOT"],
            items=items,
        )
    except _media_import.MediaImportError as error:
        flash(str(error), "danger")
        return redirect(url_for("media", tab="associate"))

    failures = result["failures"]
    if failures:
        failure = failures[0]
        title_id = failure["title_id"] or "未填写 Title ID"
        location = (
            f"第 {failure['index']} 条（{title_id}）"
            if failure["index"]
            else "写入阶段"
        )
        flash(
            f"批量录入失败，全部内容均未写入。{location}：{failure['message']}",
            "danger",
        )
    else:
        flash(f"批量录入完成：成功 {len(result['successes'])} 条。", "success")
    return redirect(url_for("media", tab="associate"))


def _read_batch_import_items() -> list[dict]:
    upload = request.files.get("json_file")
    if upload is not None and upload.filename:
        raw = upload.stream.read(MAX_BATCH_JSON_BYTES + 1)
        if len(raw) > MAX_BATCH_JSON_BYTES:
            raise _media_import.MediaImportError("JSON 文件不能超过 1 MiB。")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _media_import.MediaImportError("JSON 文件不是有效的 UTF-8 JSON 数据。") from error
        return normalize_batch_import_items(payload)

    fields = ("title_id", "season_number", "episode_number", "alist_url")
    values = {field: request.form.getlist(field) for field in fields}
    counts = {len(entries) for entries in values.values()}
    if len(counts) != 1:
        raise _media_import.MediaImportError("批量表单字段数量不一致，请刷新页面后重试。")
    row_count = counts.pop() if counts else 0
    payload = {
        "items": [
            {field: values[field][index] for field in fields}
            for index in range(row_count)
        ]
    }
    return normalize_batch_import_items(payload)


@app.post("/manager/ratings/refresh")
def refresh_manager_ratings():
    title_ids = get_rating_title_ids(get_db())
    return jsonify(
        start_rating_refresh(app.config["DATABASE"], title_ids)
    )


@app.get("/manager/ratings/refresh")
def manager_rating_refresh_status():
    return jsonify(get_rating_refresh_status())


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
        set_library_primary_artworks(
            app.config["DATABASE"],
            title_id,
            selections,
        )
    except ValueError:
        abort(404)
    flash("主图片已更新。", "success")
    return redirect(_safe_return_url(default_endpoint="media"))


@app.get("/title/<title_id>/deletion")
def preview_title_removal(title_id: str):
    try:
        plan = preview_title_deletion(
            app.config["DATABASE"], app.config["IMAGE_ROOT"], title_id, app.secret_key
        )
        preview = build_deletion_viewmodel(plan)
        response = jsonify(title_id=preview["title_id"], name=preview["name"], token=preview["token"],
                           confirm_label=preview["confirm_label"],
                           html=render_template("_title_deletion_content.html", preview=preview))
        response.headers["Cache-Control"] = "no-store"
        return response
    except ValueError as error:
        return jsonify(error=str(error)), 404
    except (OSError, sqlite3.Error):
        app.logger.exception("Title deletion preview failed")
        return jsonify(error="无法完成删除前检查，请稍后重试。"), 503


@app.post("/title/<title_id>/deletion")
def remove_title(title_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="删除请求无效。"), 400
    try:
        plan, warnings = execute_title_deletion(
            app.config["DATABASE"], app.config["IMAGE_ROOT"], title_id,
            payload.get("token"), app.secret_key
        )
    except _title_deletion.DeletionChanged as error:
        return jsonify(error=str(error)), 409
    except ValueError as error:
        return jsonify(error=str(error)), 404
    except (OSError, sqlite3.Error):
        app.logger.exception("Title deletion failed")
        return jsonify(error="删除未完成，请重新检查作品状态并重试。"), 503
    message = f"已删除《{plan['title']['display_title']}》及相关记录。"
    if warnings:
        message += "部分图片暂存文件未能清理。"
    flash(message, "warning" if warnings else "success")
    return jsonify(redirect_url=url_for("media"))


@app.post("/play/<int:file_id>")
def play_media_file(file_id: int):
    source = get_playable_file(get_db(), file_id)
    if source is None:
        abort(404)

    progress = max(int(source.get("position_ms") or 0), 0)
    duration = max(int(source.get("duration_ms") or 0), 0)
    start_position = (
        0 if is_effectively_completed(progress, duration) else progress
    )
    database_path = app.config["DATABASE"]
    playback_session = {"playback_id": None}

    def save_progress(position_ms: int, _duration_ms: int):
        playback_session["playback_id"] = save_library_playback_progress(
            database_path,
            file_id,
            position_ms,
            playback_id=playback_session["playback_id"],
        )

    video_url = alist.get_video_link(source["alist_path"])
    player.play_video(
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
