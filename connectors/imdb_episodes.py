"""IMDb 官方数据集（单集表、评分表）下载；条件请求与响应体读取均交给统一限速器。"""

import time

import requests

from connectors import throttle

URL = "https://datasets.imdbws.com/title.episode.tsv.gz"
RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
MAX_BYTES = 512 * 1024 * 1024


def download(target, *, url=URL, etag=None, progress=None):
    headers = {"Accept-Encoding": "identity"}
    if etag:
        headers["If-None-Match"] = etag

    def consume(response):
        if response.status_code == 304:
            return {"unchanged": True}
        response.raise_for_status()
        total = int(response.headers.get("Content-Length") or 0)
        if total > MAX_BYTES:
            raise ValueError("IMDb 数据文件超过允许的大小。")
        received, started = 0, time.monotonic()
        with target.open("wb") as file:
            for chunk in response.iter_content(256 * 1024):
                received += len(chunk)
                if received > MAX_BYTES or time.monotonic() - started > 600:
                    raise ValueError("IMDb 数据下载超过大小或时间上限。")
                file.write(chunk)
                if progress:
                    progress(received, total)
        if not received or (total and received != total):
            raise ValueError("IMDb 数据文件未下载完整。")
        return {"unchanged": False, "etag": response.headers.get("ETag"),
                "source_modified": response.headers.get("Last-Modified"), "download_bytes": received}

    return throttle.call("imdb", lambda: requests.get(url, headers=headers, stream=True, timeout=(10, 30)), consume=consume)
