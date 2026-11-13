"""回放扫描快照，核对文件归属；--live 使用现有 AI/TMDB 配置，数据写入独立目录。

例：uv run python scripts/evaluate_recognition.py --snapshot local-test-data/recognition-harness/before.json
    --root Special --output local-test-data/recognition-harness/run-01 --live
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()
from services import paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--root", default="Special", help="快照记录的完整路径或名称")
    parser.add_argument("--output", type=Path, required=True, help="新建的独立评测目录")
    parser.add_argument("--labels", type=Path, help="可选人工标签 JSON：{相对路径: 作品组名}")
    parser.add_argument("--live", action="store_true", help="真实请求现有 AI/TMDB，不访问网盘、不写业务库")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出目录已存在，请换一个新目录以保留评测证据。")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    matching = [record for record in snapshot["records"] if record["path"] == args.root or record["name"] == args.root]
    if len(matching) != 1:
        parser.error("root 必须唯一匹配一条快照记录。")
    original = matching[0]
    args.output.mkdir(parents=True)
    load_dotenv(paths.ENV_PATH, override=False)
    production_local = Path(os.environ.get("MOVIEDB_LOCAL_DATABASE") or paths.DATA_DIR / "local.db")
    database = args.output / "library.db"
    if args.live:
        source = paths.database_path().resolve()
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as reader, sqlite3.connect(database) as target:
            reader.backup(target)
    os.environ["MOVIEDB_LOCAL_DATABASE"] = str((args.output / "local.db").resolve())
    os.environ["MOVIEDB_KEEPALIVE_DISABLED"] = "1"
    from models import local_store
    from services import media_grouping, recognition, recognition_harness
    if args.live and production_local.exists():
        # 只复用非凭据查询缓存，不复制挂载、令牌、用户别名或任务。
        with sqlite3.connect(production_local.resolve().as_uri() + "?mode=ro", uri=True) as reader:
            for key, payload, expiry in reader.execute("SELECT key, payload, expires_at FROM lookup_cache WHERE expires_at > ?", (time.time(),)):
                if key.startswith(("search:", "summary:")):
                    local_store.cache_put(database, key, json.loads(payload), expiry - time.time())
    started = time.monotonic()
    groups = media_grouping.partition(original["path"], original["files"])
    if args.live:
        groups = recognition_harness.plan_groups(database, original["path"], groups)
    observed = Counter(item["path"] for group in groups for item in group["files"])
    expected = Counter(item["path"] for item in original["files"])
    if observed != expected:
        raise RuntimeError("分组遗漏或重复了文件，停止评测。")
    stats, results = {}, {}
    if args.live:
        stats = recognition.recognize_many(database, groups, workers=4,
                on_result=lambda record, result: results.update({record["path"]: result}))
    for group in groups:
        if group["path"] in results:
            group["recognition"] = results[group["path"]]
    summary = {"mode": "live" if args.live else "offline", "files": sum(expected.values()), "groups": len(groups),
               "missing": 0, "duplicates": 0, "states": dict(Counter(result["status"] for result in results.values())),
               "recognition_stats": stats, "elapsed_seconds": round(time.monotonic() - started, 3)}
    if args.labels:
        labels = json.loads(args.labels.read_text(encoding="utf-8-sig"))
        from pathlib import PurePosixPath
        predicted = {str(PurePosixPath(item["path"]).relative_to(original["path"])): index
                     for index, group in enumerate(groups) for item in group["files"]}
        if set(labels) != set(predicted):
            raise ValueError("人工标签必须覆盖快照内全部文件，不能缺失或多出。")
        pairs = [(left, right) for i, left in enumerate(sorted(labels)) for right in sorted(labels)[i + 1:]]
        wrong_merge = sum(predicted[a] == predicted[b] and labels[a] != labels[b] for a, b in pairs)
        wrong_split = sum(predicted[a] != predicted[b] and labels[a] == labels[b] for a, b in pairs)
        summary["label_checks"] = {"wrong_merge_pairs": wrong_merge, "wrong_split_pairs": wrong_split}
    report = {"summary": summary, "source": str(args.snapshot), "records": groups}
    (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 识别回放", "", f"模式：{summary['mode']}；文件 {summary['files']}；分组 {summary['groups']}；遗漏 0；重复 0。",
             "", "| 组 | 文件数 | 识别作品 | IMDb | 状态 |", "|---|---:|---|---|---|"]
    for group in groups:
        result = group.get("recognition") or {}
        title = result.get("title") or {}
        lines.append(f"| {group['name'].replace('|', '／')} | {len(group['files'])} | {title.get('name', '')} | {title.get('title_id', '')} | {result.get('status', '')} |")
    lines.extend(["", "文件归属检查不等同于作品身份或季集准确率；后两者需要独立人工标签。", ""])
    (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
