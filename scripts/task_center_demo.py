"""后台任务圆钮的本地演示：用真实的模板、样式和 task-center.js，后台任务接口换成按时间推进的模拟数据。

    uv run python scripts/task_center_demo.py            # 打开 http://127.0.0.1:5070
    uv run python scripts/task_center_demo.py --port 5080

只读本机媒体库的片名和海报做演示，不写任何数据库，也不调用外部服务。
展开关联任务时读取的 /manager/tasks/detail 也是模拟的，字段与 viewmodels/tasks.py 的 import_detail 一致。
页面参数（截图也用它们）：scenario 场景、at 场景已进行的秒数、open=1 展开任务列表、expand=1 / failed 展开第一个（失败的）任务、
live=1 让圆钮保持“实时活动”胶囊、fly=1 冻结在海报飞行途中、theme=light 浅色主题。
/device?w=390&h=844&scenario=… 用固定宽高的框模拟手机屏幕。
"""

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import time

from flask import Flask, jsonify, render_template_string, request

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "none": "无任务",
    "single": "单个关联（约 20 秒完成）",
    "multi": "5 个关联排队，依次完成",
    "mixed": "关联 + 扫描 + 评分",
    "failed": "失败与需处理",
    "waiting": "等待接口恢复",
}
FALLBACK_NAMES = ["流浪地球2", "长津湖", "八佰", "赛博朋克：边缘行者", "人生切割术", "杀戮人机", "同乐者", "群星"]


def library_titles(limit=8):
    """本机媒体库里有海报的作品（片名、相对 static 的海报路径）；没有媒体库时用占位名。"""
    posters = sorted((ROOT / "static" / "images").glob("tt*/posters/*.jpg"))
    picked, seen = [], set()
    for path in posters:
        title_id = path.parts[-3]
        if title_id not in seen:
            seen.add(title_id)
            picked.append((title_id, path.relative_to(ROOT / "static").as_posix()))
    names = {}
    database = ROOT / "library.db"
    if picked and database.is_file():
        marks = ", ".join("?" for _ in picked)
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            try:
                names = dict(db.execute(f"""
                    SELECT title_id, display_title
                      FROM title_basic
                     WHERE title_id IN ({marks})
                """, [title_id for title_id, _ in picked]))
            except sqlite3.OperationalError:
                names = {}
    titles = [(names.get(title_id) or FALLBACK_NAMES[index % len(FALLBACK_NAMES)], poster, title_id)
              for index, (title_id, poster) in enumerate(picked) if names.get(title_id) or not names]
    titles = titles[:limit]
    while len(titles) < limit:
        titles.append((FALLBACK_NAMES[len(titles) % len(FALLBACK_NAMES)], None, f"tt{9900000 + len(titles)}"))
    return titles


class Demo:
    """场景 = 一组按开始时间推进的任务；重试、停止、确认关联会在场景上追加或改写任务。"""

    def __init__(self):
        self.titles = library_titles()
        self.set("single")

    def set(self, name, at=0.0):
        self.name = name if name in SCENARIOS else "single"
        self.start = time.time() - at
        self.overrides = {}
        self.extra = []

    def job(self, index, offset, duration, files, outcome="complete", series=True):
        name, poster, title_id = self.titles[index % len(self.titles)]
        return {"index": index, "id": f"import:{title_id}:{int(self.start)}", "name": name, "poster": poster,
                "start": self.start + offset, "duration": duration, "files": files, "outcome": outcome, "series": series}

    def jobs(self):
        plans = {
            "none": [],
            "single": [self.job(0, 0, 20, 12)],
            # 一个接一个开始：前一个完成滑出，后面排队的往前接上
            "multi": [self.job(1, 0, 10, 1, series=False), self.job(2, 10, 10, 1, series=False), self.job(4, 20, 10, 6),
                      self.job(5, 30, 10, 1, series=False), self.job(3, 40, 600, 40)],
            "mixed": [self.job(3, 0, 600, 40)],
            "failed": [self.job(0, -30, 5, 1, "failed", series=False), self.job(4, -30, 5, 9, "partial"), self.job(5, 0, 600, 8)],
            "waiting": [self.job(2, 0, 600, 1, "waiting", series=False)],
        }[self.name] + self.extra
        return [self.overrides.get(job["id"], job) for job in jobs_by_id(plans)]

    def submit(self, indexes):
        now = time.time()
        added = []
        for index in indexes:
            job = self.job(index, 0, 16 + len(added) * 4, 1, series=False)
            job.update(id=f"import:{self.titles[index % len(self.titles)][2]}:{int(now * 1000)}", start=now)
            self.extra.append(job)
            added.append(job)
        return added

    def retry(self, task_id):
        for job in self.jobs():
            if job["id"] == task_id:
                self.overrides[task_id] = {**job, "start": time.time(), "duration": 10, "outcome": "complete"}

    def cancel(self, task_id):
        for job in self.jobs():
            if job["id"] == task_id:
                self.overrides[task_id] = {**job, "outcome": "cancelled", "duration": max(0.0, time.time() - job["start"])}


def jobs_by_id(jobs):
    return list({job["id"]: job for job in jobs}.values())


def import_task(job, now):
    files, elapsed = job["files"], now - job["start"]
    task = {"id": job["id"], "kind": "import", "name": job["name"], "poster": job["poster"], "files": files,
            "core": 0, "linked": 0, "done": 0, "total": 0, "current": [], "current_more": 0, "issues": [], "waits": {},
            "retry_at": 0, "attempts": 1, "failed": 0, "cancel_ids": [], "retry_ids": [], "jobs": [],
            "updated_at": job["start"] + job["duration"]}
    if job["outcome"] == "waiting":
        until = job["start"] + 45 + 45 * int(max(0, elapsed) // 45)
        task.update(state="waiting", phase="retry", retry_at=until, attempts=2,
                    waits={"tmdb": {"reason": "请求过于频繁，稍后自动重试", "until": until}}, cancel_ids=[f"{job['id']}#job"])
        return task
    if elapsed < 2:
        task.update(state="queued", phase="queued", cancel_ids=[f"{job['id']}#job"])
        return task
    if elapsed < job["duration"]:
        # 关联四步：核对资料 → 获取链接 → 读取参数 → 写入媒体库
        progress = (elapsed - 2) / max(1, job["duration"] - 2)
        share = lambda start, end: min(files, int(files * (progress - start) / (end - start)))
        if progress < 0.1:
            task.update(phase="metadata")
        elif progress < 0.3:
            task.update(phase="metadata", linked=share(0.1, 0.3))
        elif progress < 0.8:
            done = share(0.3, 0.8)
            task.update(phase="probe", done=done, total=files, linked=files,
                        current=[f"S01E{done + 1:02d}" if job["series"] else job["name"]])
        else:
            task.update(phase="write", done=files, total=files, linked=files, core=min(files - 1, share(0.8, 1.0)))
        task.update(state="running", cancel_ids=[f"{job['id']}#job"])
        return task
    outcome = job["outcome"]
    task.update(state=outcome, phase=outcome, done=files, total=files, core=files if outcome != "failed" else 0,
                linked=files if outcome != "failed" else 0)
    if outcome == "failed":
        task.update(failed=files, retry_ids=[f"{job['id']}#job"],
                    issues=[{"label": "S01E01" if job["series"] else "", "reason": "视频链接已失效，请重新扫描后再关联"}])
    elif outcome == "partial":
        task.update(retry_ids=[f"{job['id']}#job"], issues=[{"label": "海报", "reason": "TMDB 未返回中文海报，已保留原图"}])
    return task


PROBE_SAMPLES = ["HDR10 · 3840×2160 · HEVC · 5.1", "1920×1080 · H.264 · 立体声", "Dolby Vision · 3840×2160 · HEVC · 7.1"]


def import_detail(job, now):
    """展开关联任务时的详情：与 viewmodels/tasks.py 的 import_detail 同一形状（逐集文件、正在读取、失败摘要）。"""
    task = import_task(job, now)
    count, series = job["files"], job["series"]
    probed = task["files"] if task["state"] not in ("running", "queued", "waiting") else task["done"] if task["phase"] in ("probe", "write") else 0
    files = []
    for index in range(count):
        season, episode = index // 10 + 1, index % 10 + 1
        position = f"S{season:02}E{episode:02}" if series else ""
        name = f"{job['name']}.{position}.2160p.WEB-DL.mkv" if series else f"{job['name']}.2023.2160p.BluRay.mkv"
        sample = PROBE_SAMPLES[index % len(PROBE_SAMPLES)]
        if task["state"] == "failed":
            state, detail = ("failed", "视频链接已失效，请重新扫描后再关联") if index == 0 else ("skipped", "同批文件出错，本文件未写入")
        elif job["outcome"] == "partial" and task["state"] == "partial" and index >= count - 2:
            state, detail = "failed", "读取参数超时"
        elif index < probed:
            state, detail = "done", sample
        elif index == probed and task["phase"] == "probe":
            state, detail = "active", "正在读取参数…"
        else:
            state, detail = "pending", "等待中"
        files.append({"name": name, "position": position, "season": season if series else None, "state": state,
                      "detail": detail, "written": task["state"] in ("complete", "partial") and state == "done"})
    current = next((file for file in files if file["state"] == "active"), None)
    written = sum(file["written"] for file in files)
    failed = sum(file["state"] in ("failed", "skipped") for file in files)
    unit = "集" if series else "个视频"
    outcome = (f"已入库 {written} {unit}，{failed} {unit}失败" if written else f"{failed} {unit}失败") if failed else ""
    return {"id": job["id"], "name": job["name"], "meta": " · ".join(["剧集" if series else "电影", "2023", f"{count} 个视频"]),
            "poster": job["poster"], "media_type": "series" if series else "movie", "state": task["state"],
            "files": files, "series": series, "outcome": outcome,
            "current": " · ".join(part for part in (current["position"], current["name"]) if part) if current else ""}


def snapshot(demo, now):
    tasks = [import_task(job, now) for job in demo.jobs()]
    elapsed = now - demo.start
    if demo.name == "mixed":
        tasks.append({"id": "scan", "kind": "scan", "name": "扫描媒体", "phase": "recognizing", "state": "running",
                      "done": 0, "total": 0, "detail": "AI 2 · 核对 1", "issues": [], "updated_at": demo.start})
        tasks.append({"id": "rating", "kind": "rating", "name": "刷新评分", "phase": "rating", "state": "running",
                      "done": min(40, int(elapsed * 2)), "total": 40, "detail": demo.titles[1][0], "issues": [], "updated_at": demo.start})
    services = []
    for task in tasks:
        for service, wait in task.get("waits", {}).items():
            services.append({"service": service, "name": "TMDB", **wait})
    return {"tasks": tasks, "services": services,
            "active": any(task["state"] in ("running", "queued", "waiting") for task in tasks)}


PAGE = """<!DOCTYPE html>
<html lang="zh-CN" data-bs-theme="{{ theme }}">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="initial-scale=1, width=device-width">
    <link rel="icon" href="data:,">
    <link href="{{ url_for('static', filename='vendor/bootstrap.min.css') }}" rel="stylesheet">
    <link href="{{ url_for('static', filename='style.css') }}" rel="stylesheet">
    <title>后台任务圆钮演示</title>
    <style>
        .demo-shell { max-width: 960px; margin: 0 auto; padding: 6.5rem 1.25rem 4rem; }
        .demo-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr)); gap: 1rem; }
        .demo-card { position: relative; padding: 0; border: 0; border-radius: var(--radius-thumb); overflow: hidden; aspect-ratio: 2 / 3;
                     background: var(--ui-fill); color: var(--bs-secondary-color); outline: 2px solid transparent; outline-offset: 3px; }
        .demo-card img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
        .demo-card[aria-pressed="true"] { outline-color: var(--media-accent); }
        .demo-card span { position: absolute; left: 0; right: 0; bottom: 0; padding: 1.5rem .5rem .5rem; font-size: .75rem; text-align: left;
                          color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.75)); }
    </style>
</head>
<body data-icon-url="{{ url_for('static', filename='vendor/lucide/sprite.svg') }}">
    <button class="site-menu-toggle" type="button" aria-label="（演示）导航">
        <svg class="app-icon" width="16" height="16" aria-hidden="true"><use href="{{ url_for('static', filename='vendor/lucide/sprite.svg') }}#menu"></use></svg>
    </button>
    <main class="demo-shell">
        <h1 class="h3 mb-2">后台任务圆钮演示</h1>
        <p class="text-body-secondary mb-4">左上角菜单按钮旁的小圆钮就是后台任务。任务数据是模拟的，按时间自动推进；片名和海报取自本机媒体库，不会写入任何数据。点圆钮，任务在下方排成一列胶囊卡片；窗口窄于 576px 时卡片铺满屏宽。</p>

        <h2 class="h6 text-body-secondary mb-2">场景（切换会重新开始计时）</h2>
        <div class="d-flex flex-wrap gap-2 mb-4">
            {% for key, label in scenarios.items() %}
                <a class="btn btn-sm chip-btn{% if key == scenario %} active{% endif %}" href="?scenario={{ key }}" data-demo-scenario>{{ label }}</a>
            {% endfor %}
        </div>

        <h2 class="h6 text-body-secondary mb-2">模拟操作</h2>
        <div class="d-flex flex-wrap gap-2 mb-4">
            <button class="btn btn-sm media-primary-action" type="button" data-demo-submit>确认关联选中的海报（飞入）</button>
            <button class="btn btn-sm chip-btn" type="button" data-demo-done>本页操作完成（实时活动胶囊）</button>
            <button class="btn btn-sm chip-btn" type="button" data-demo-theme>切换明暗</button>
        </div>

        <h2 class="h6 text-body-secondary mb-2">点选海报，再点“确认关联”</h2>
        <div class="demo-cards">
            {% for name, poster, _ in titles %}
                <button class="demo-card" type="button" aria-pressed="{{ 'true' if loop.index0 < 3 else 'false' }}" data-index="{{ loop.index0 }}" data-name="{{ name }}">
                    {% if poster %}<img src="{{ url_for('static', filename=poster) }}" alt="">{% endif %}
                    <span>{{ name }}</span>
                </button>
            {% endfor %}
        </div>
    </main>
    {% include "_task_island.html" %}
    <script src="{{ url_for('static', filename='vendor/bootstrap.bundle.min.js') }}"></script>
    <script src="{{ url_for('static', filename='site.js') }}"></script>
    <script src="{{ url_for('static', filename='status-bar.js') }}"></script>
    <script>
        // 切换场景前清掉任务中心存在本标签页的状态，让每个场景都从头开始。
        for (const key of ["tasks-seen", "tasks-dismissed", "tasks-posters", "tasks-state"]) sessionStorage.removeItem(key);
    </script>
    <script src="{{ url_for('static', filename='task-center.js') }}"></script>
    <script>
    (() => {
        const params = new URLSearchParams(location.search);
        const orb = document.querySelector("[data-task-orb]");
        const wait = (test, then) => { const tick = () => test() ? then() : setTimeout(tick, 50); tick(); };
        const cards = [...document.querySelectorAll(".demo-card")];
        cards.forEach(card => card.addEventListener("click", () => card.setAttribute("aria-pressed", String(card.getAttribute("aria-pressed") !== "true"))));

        async function submit() {
            const picked = cards.filter(card => card.getAttribute("aria-pressed") === "true");
            if (!picked.length) return;
            const sources = picked.map(card => ({ rect: card.getBoundingClientRect(), src: card.querySelector("img")?.currentSrc || "" }));
            const response = await fetch("/demo/submit", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ indexes: picked.map(card => Number(card.dataset.index)) }) });
            const groups = await response.json();
            TaskCenter.arrive(groups.map((group, index) => ({ ...group, poster: sources[index].src })), sources);
            // fly=1：把刚出发的动画拨到 flyAt 毫秒处定格，用来截飞行途中的一帧
            if (params.get("fly") === "1") document.getAnimations().forEach(animation => { animation.pause(); animation.currentTime = Number(params.get("flyAt") || 330); });
            picked.forEach(card => card.setAttribute("aria-pressed", "false"));
        }
        let counter = 0;
        function done() {
            const key = `demo-local-${++counter}`;
            TaskCenter.progress(key, { text: "刷新评分", done: 6, total: 10 });
            setTimeout(() => TaskCenter.result(key, { tone: "success", text: "评分已刷新" }), 300);
        }
        document.querySelector("[data-demo-submit]").addEventListener("click", submit);
        document.querySelector("[data-demo-done]").addEventListener("click", done);
        document.querySelector("[data-demo-theme]").addEventListener("click", () => {
            const root = document.documentElement;
            root.dataset.bsTheme = root.dataset.bsTheme === "dark" ? "light" : "dark";
        });

        // 截图用：等圆钮出现后，按参数打开任务列表、展开任务、保持胶囊或冻结飞行途中的一帧。
        const ready = () => !orb.hidden && orb.getAttribute("aria-label");
        // expand=1 展开第一个任务，expand=failed 展开第一个失败的任务
        const row = () => document.querySelector(params.get("expand") === "failed" ? ".island-card.is-failed" : ".island-card");
        // 卡片只在列表打开后才生成：先打开，再按参数展开某一张
        if (params.get("open") === "1") wait(ready, () => { TaskCenter.open(); if (params.get("expand")) wait(row, () => TaskCenter.open(row().dataset.taskId)); });
        if (params.get("live") === "1") wait(ready, () => { done(); setInterval(done, 2000); });
        if (params.get("fly") === "1") setTimeout(submit, 800);
    })();
    </script>
</body>
</html>
"""


def create_app():
    app = Flask(__name__, root_path=str(ROOT), template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.json.ensure_ascii = False
    # _ui.html 里其他宏用到的过滤器；演示页不用，给个空实现让模板能加载。
    app.jinja_env.filters["image_tone"] = lambda path: None
    demo = Demo()

    @app.get("/")
    def page():
        if "scenario" in request.args:
            demo.set(request.args["scenario"], float(request.args.get("at") or 0))
        return render_template_string(PAGE, scenarios=SCENARIOS, scenario=demo.name, titles=demo.titles,
                                      theme=request.args.get("theme", "dark"))

    # 手机尺寸预览：把演示页装进固定宽高的 iframe，窄屏规则（列表铺满屏宽等）按 iframe 宽度生效。
    @app.get("/device")
    def device():
        width, height = int(request.args.get("w", 390)), int(request.args.get("h", 844))
        query = request.query_string.decode().replace(f"w={request.args.get('w')}", "").replace(f"h={request.args.get('h')}", "")
        return (f'<!DOCTYPE html><html><body style="margin:0;background:#111">'
                f'<iframe src="/?{query.strip("&")}" style="display:block;width:{width}px;height:{height}px;border:0"></iframe></body></html>')

    @app.get("/manager/tasks")
    def background_tasks():
        return jsonify(snapshot(demo, time.time()))

    @app.get("/manager/tasks/detail")
    def background_task_detail():
        job = next((job for job in demo.jobs() if job["id"] == request.args.get("id")), None)
        if job is None:
            return jsonify(error="任务已结束或已移除。"), 404
        return jsonify(import_detail(job, time.time()))

    # 完成时“打开作品页”的链接目标；演示页没有作品页，回到首页
    @app.get("/title/<title_id>")
    def title_detail(title_id):
        return page()

    @app.post("/manager/association/jobs")
    def association_jobs():
        payload = request.get_json(silent=True) or {}
        for job_id in payload.get("ids") or []:
            task_id = str(job_id).split("#", 1)[0]
            demo.retry(task_id) if payload.get("action") == "retry" else demo.cancel(task_id)
        return jsonify(ok=True)

    @app.post("/demo/scan")
    def discover_import_media():
        return jsonify(ok=True)

    @app.post("/demo/rating")
    def refresh_manager_ratings():
        return jsonify(ok=True)

    @app.post("/demo/submit")
    def submit():
        indexes = [int(index) for index in (request.get_json(silent=True) or {}).get("indexes", [])][:6]
        return jsonify([{"id": job["id"], "name": job["name"], "files": job["files"]} for job in demo.submit(indexes)])

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=5070)
    args = parser.parse_args()
    create_app().run(host="127.0.0.1", port=args.port, debug=False)
