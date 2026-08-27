import os
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
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


def get_movie_mappings():
    """Add browser URLs to the stored movie-source relationships."""
    mappings = models.get_movie_mappings(app.config["DATABASE"])
    for mapping in mappings:
        mapping["alist_url"] = helpers.build_alist_url(mapping["alist_path"])

    return mappings


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        movie_name = request.form.get("movie_name", "").strip()
        alist_link = request.form.get("alist_url", "").strip()

        if not movie_name or not alist_link:
            flash("请同时填写电影名称和 AList 视频直链。", "danger")
            return redirect(url_for("index"))

        try:
            alist_path = helpers.parse_alist_url(alist_link)
            helpers.validate_alist_video_url(alist_link)

            search_results = helpers.search_movie(movie_name).get("results", [])
            if not search_results:
                raise ValueError("TMDB 中未找到对应电影。")

            movie_id = int(search_results[0]["id"])
            details = helpers.search_id(movie_id)
            if int(details.get("id", 0)) != movie_id:
                raise ValueError("TMDB 电影详情返回异常。")

            image_links = helpers.search_img(movie_id)
            local_images = helpers.download_movie_images(
                movie_id,
                image_links,
                app.config["IMAGE_ROOT"],
            )
            models.save_movie(
                app.config["DATABASE"],
                details,
                local_images,
                alist_path,
            )

            flash(
                f"已关联《{details.get('title') or movie_name}》与 AList 文件。",
                "success",
            )
        except ValueError as error:
            flash(str(error), "danger")
        except Exception:
            app.logger.exception("Importing movie failed")
            flash("查询或保存失败，请检查 TMDB、AList 和数据库状态。", "danger")

        return redirect(url_for("index"))

    return render_template(
        "index.html",
        movie_mappings=get_movie_mappings(),
        alist_origin=helpers.alist_url,
    )


if __name__ == "__main__":
    app.run(debug=True)
