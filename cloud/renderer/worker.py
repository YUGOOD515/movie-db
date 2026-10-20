"""MovieDB 只读渲染 Worker（Python Workers）。

只由 moviedb-router 通过服务绑定调用，用于渲染未预先生成的页面（如任意筛选组合）。
运行的是与本地相同的 Flask 应用（只读模式），数据库与快照元数据来自 R2。
"""

import json
import os

os.environ["MOVIEDB_READ_ONLY"] = "1"
os.environ["MOVIEDB_DATABASE"] = "/tmp/library.db"
# Worker 启动阶段不允许生成随机数；只读模式不写会话，固定密钥即可。
os.environ.setdefault("FLASK_SECRET_KEY", "moviedb-readonly-snapshot")

from workers import Response, WorkerEntrypoint, wsgi  # noqa: E402

import app as movie_app  # noqa: E402

STATE = {"version": None}


async def load_snapshot(env, version: str | None) -> bool:
    if STATE["version"] is not None and (version is None or version == STATE["version"]):
        return True
    meta_object = await env.SNAPSHOT.get("data/meta.json")
    database_object = await env.SNAPSHOT.get("data/library.db")
    if meta_object is None or database_object is None:
        return False
    meta = json.loads(await meta_object.text())
    data = await database_object.arrayBuffer()
    raw = data.to_bytes() if hasattr(data, "to_bytes") else bytes(data)
    with open(movie_app.app.config["DATABASE"], "wb") as file:
        file.write(raw)
    movie_app.app.config.update(
        SNAPSHOT_AT=meta.get("snapshot_at", ""),
        ALIST_ORIGIN=meta.get("alist_origin", ""),
        THUMBNAIL_INDEX=frozenset(meta.get("thumbnails", [])),
    )
    STATE["version"] = meta.get("version")
    return True


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if not await load_snapshot(self.env, request.headers.get("X-Snapshot-Version")):
            return Response("只读快照尚未同步。", status=503)
        return await wsgi.fetch(movie_app.app, request, self.env)
