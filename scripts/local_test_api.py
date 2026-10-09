"""Offline TMDb/MDBList replay for Breaking Bad; never calls remote services."""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file
from werkzeug.serving import WSGIRequestHandler

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "local-test-data" / "breaking-bad"


class LocalRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        # Keep API keys in query parameters out of the local access log.
        self.log("info", '"%s %s %s" %s %s', self.command,
                 self.path.split("?", 1)[0], self.request_version, code, size)


def create_app(data_root=DEFAULT_DATA):
    root = Path(data_root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    payloads = json.loads((root / "tmdb.json").read_text(encoding="utf-8"))
    ratings = json.loads((root / "mdblist.json").read_text(encoding="utf-8"))
    app = Flask(__name__)
    app.json.ensure_ascii = False

    @app.get("/health")
    def health():
        return jsonify(status="ok", imdb_id=manifest["imdb_id"],
                       captured_at=manifest["captured_at"], images=len(manifest["images"]))

    @app.get("/3/<path:endpoint>")
    def tmdb(endpoint):
        if request.args.get("language", "zh-CN") != "zh-CN":
            abort(404, description="Only the saved zh-CN snapshot is available.")
        if endpoint in ("search/tv", "search/multi"):
            query = "".join(request.args.get("query", "").casefold().split())
            aliases = ("绝命毒师", "絕命毒師", "breakingbad")
            matched = query and any(query in name for name in aliases)
            page = request.args.get("page", 1, type=int)
            results = deepcopy(payloads["search"]["results"]) if matched and page == 1 else []
            if endpoint == "search/multi":
                for result in results:
                    result["media_type"] = "tv"
            return jsonify(page=page, results=results, total_pages=int(bool(matched)),
                           total_results=int(bool(matched)))
        if endpoint.startswith("search/"):
            return jsonify(page=1, results=[], total_pages=0, total_results=0)
        if endpoint.startswith("find/"):
            title_id = endpoint.removeprefix("find/")
            if title_id not in payloads["find"]:
                return jsonify(movie_results=[], tv_results=[], tv_episode_results=[],
                               tv_season_results=[], person_results=[])
            return jsonify(payloads["find"][title_id])
        resources = payloads["resources"]
        if endpoint not in resources:
            abort(404)
        result = deepcopy(resources[endpoint])
        for resource in filter(None, request.args.get("append_to_response", "").split(",")):
            key = f"{endpoint}/{resource}"
            if key not in resources:
                abort(404, description="Requested resource is not in the snapshot.")
            result[resource] = deepcopy(resources[key])
        return jsonify(result)

    @app.get("/mdblist/api/")
    def mdblist():
        if request.args.get("i") != manifest["imdb_id"]:
            return jsonify(error="Title not in the local snapshot"), 404
        return jsonify(ratings)

    @app.get("/t/p/<size>/<filename>")
    def image(size, filename):
        relative = manifest["images"].get(f"{size}/{filename}")
        if relative is None:
            abort(404)
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            abort(404)
        return send_file(path)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    create_app(args.data).run(host="127.0.0.1", port=args.port, debug=False,
                            request_handler=LocalRequestHandler)


if __name__ == "__main__":
    main()
