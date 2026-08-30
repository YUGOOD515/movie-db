# -*- coding: utf-8 -*-
"""Import IMDb TSV datasets into imdb.db.

Streams each TSV under imdb/, converts the ``\\N`` marker to NULL, and
bulk-loads rows into WITHOUT ROWID tables keyed by the datasets' natural
identifiers. ``title_akas`` keeps only Chinese/English rows (language starts
with zh/en, or region belongs to a Chinese/English-speaking market);
``title.principals`` is skipped for now. Secondary indexes are built after
each table's data load.

Usage:
    uv run python scripts/import_imdb.py
    uv run python scripts/import_imdb.py --tables title_ratings name_basics
"""
import argparse
import logging
import sqlite3
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMDB_DIR = PROJECT_ROOT / "imdb"
DATABASE_PATH = PROJECT_ROOT / "imdb.db"

BATCH_SIZE = 100_000
PROGRESS_EVERY = 1_000_000
NULL_MARKER = "\\N"
ZH_EN_REGIONS = {"CN", "TW", "HK", "MO", "US", "GB", "AU", "CA", "NZ", "IE"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import_imdb")

# 每张表：源文件、列定义（名称 + 类型）、自然主键、二级索引、行过滤器。
TABLES = [
    {
        "name": "name_basics",
        "file": "name.basics.tsv",
        "columns": [
            ("nconst", "text"),
            ("primaryName", "text"),
            ("birthYear", "int"),
            ("deathYear", "int"),
            ("primaryProfession", "text"),
            ("knownForTitles", "text"),
        ],
        "primary_key": ["nconst"],
        "indexes": {
            "idx_name_basics_primary_name": ["primaryName"],
        },
        "filter": None,
    },
    {
        "name": "title_basics",
        "file": "title.basics.tsv",
        "columns": [
            ("tconst", "text"),
            ("titleType", "text"),
            ("primaryTitle", "text"),
            ("originalTitle", "text"),
            ("isAdult", "int"),
            ("startYear", "int"),
            ("endYear", "int"),
            ("runtimeMinutes", "int"),
            ("genres", "text"),
        ],
        "primary_key": ["tconst"],
        "indexes": {
            "idx_title_basics_primary_title": ["primaryTitle"],
            "idx_title_basics_title_type": ["titleType"],
        },
        "filter": None,
    },
    {
        "name": "title_akas",
        "file": "title.akas.tsv",
        "columns": [
            ("titleId", "text"),
            ("ordering", "int"),
            ("title", "text"),
            ("region", "text"),
            ("language", "text"),
            ("types", "text"),
            ("attributes", "text"),
            ("isOriginalTitle", "int"),
        ],
        "primary_key": ["titleId", "ordering"],
        "indexes": {
            "idx_title_akas_title": ["title"],
        },
        # 方案B：语言以 zh/en 开头，或地区属于中文区/英语区；不单独保留原始标题行。
        "filter": lambda row: row[4].startswith(("zh", "en"))
        or row[3] in ZH_EN_REGIONS,
    },
    {
        "name": "title_crew",
        "file": "title.crew.tsv",
        "columns": [
            ("tconst", "text"),
            ("directors", "text"),
            ("writers", "text"),
        ],
        "primary_key": ["tconst"],
        "indexes": {},
        "filter": None,
    },
    {
        "name": "title_episode",
        "file": "title.episode.tsv",
        "columns": [
            ("tconst", "text"),
            ("parentTconst", "text"),
            ("seasonNumber", "int"),
            ("episodeNumber", "int"),
        ],
        "primary_key": ["tconst"],
        "indexes": {
            "idx_title_episode_parent": [
                "parentTconst",
                "seasonNumber",
                "episodeNumber",
            ],
        },
        "filter": None,
    },
    {
        "name": "title_ratings",
        "file": "title.ratings.tsv",
        "columns": [
            ("tconst", "text"),
            ("averageRating", "float"),
            ("numVotes", "int"),
        ],
        "primary_key": ["tconst"],
        "indexes": {},
        "filter": None,
    },
]


def to_int(value):
    try:
        return int(value)
    except ValueError:
        return None


def to_float(value):
    try:
        return float(value)
    except ValueError:
        return None


def make_converter(kind):
    if kind == "int":
        return lambda v: None if v in (NULL_MARKER, "") else to_int(v)
    if kind == "float":
        return lambda v: None if v in (NULL_MARKER, "") else to_float(v)
    return lambda v: None if v == NULL_MARKER else v


def build_create_sql(table):
    type_map = {"text": "TEXT", "int": "INTEGER", "float": "REAL"}
    column_sql = ",\n    ".join(
        f"{name} {type_map[kind]}" for name, kind in table["columns"]
    )
    primary_key = ", ".join(table["primary_key"])
    return (
        f"CREATE TABLE {table['name']} (\n"
        f"    {column_sql},\n"
        f"    PRIMARY KEY ({primary_key})\n"
        f") WITHOUT ROWID"
    )


def configure_connection(conn):
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -512000")
    conn.execute("PRAGMA locking_mode = EXCLUSIVE")


def import_table(conn, table):
    path = IMDB_DIR / table["file"]
    columns = table["columns"]
    column_count = len(columns)
    converters = [make_converter(kind) for _, kind in columns]
    filter_fn = table["filter"]
    insert_sql = (
        f"INSERT OR REPLACE INTO {table['name']} "
        f"VALUES ({', '.join('?' * column_count)})"
    )

    conn.execute(f"DROP TABLE IF EXISTS {table['name']}")
    conn.execute(build_create_sql(table))
    conn.commit()

    scanned = kept = bad = 0
    batch = []
    started = time.monotonic()
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        fh.readline()  # 表头
        for line in fh:
            scanned += 1
            row = line.rstrip("\r\n").split("\t")
            if len(row) != column_count:
                bad += 1
                continue
            if filter_fn is not None and not filter_fn(row):
                continue
            batch.append(
                tuple(conv(value) for conv, value in zip(converters, row))
            )
            kept += 1
            if len(batch) >= BATCH_SIZE:
                conn.executemany(insert_sql, batch)
                conn.commit()
                batch.clear()
            if kept % PROGRESS_EVERY == 0:
                logger.info(
                    "%s: 已写入 %s 行（扫描 %s 行，%.1f 分钟）",
                    table["name"],
                    f"{kept:,}",
                    f"{scanned:,}",
                    (time.monotonic() - started) / 60,
                )

    if batch:
        conn.executemany(insert_sql, batch)
        conn.commit()

    elapsed = time.monotonic() - started
    logger.info(
        "%s: 完成，写入 %s 行（扫描 %s 行，丢弃 %s 行），耗时 %.1f 分钟",
        table["name"],
        f"{kept:,}",
        f"{scanned:,}",
        f"{bad:,}",
        elapsed / 60,
    )
    return {"scanned": scanned, "kept": kept, "bad": bad}


def build_indexes(conn, table):
    for index_name, index_columns in table["indexes"].items():
        started = time.monotonic()
        conn.execute(
            f"CREATE INDEX {index_name} "
            f"ON {table['name']} ({', '.join(index_columns)})"
        )
        conn.commit()
        logger.info(
            "%s: 索引 %s 完成，耗时 %.1f 分钟",
            table["name"],
            index_name,
            (time.monotonic() - started) / 60,
        )
    conn.execute(f"ANALYZE {table['name']}")
    conn.commit()


def verify(conn, selected):
    logger.info("===== 校验 =====")
    for table in selected:
        rows = conn.execute(
            f"SELECT COUNT(*) FROM {table['name']}"
        ).fetchone()[0]
        logger.info("行数 %s: %s", table["name"], f"{rows:,}")

    checks = [
        (
            "SELECT tconst, titleType, primaryTitle, startYear, genres "
            "FROM title_basics WHERE tconst = 'tt0111161'",
            "title_basics 抽查（肖申克的救赎）",
        ),
        (
            "SELECT tconst, averageRating, numVotes "
            "FROM title_ratings WHERE tconst = 'tt0111161'",
            "title_ratings 抽查",
        ),
        (
            "SELECT nconst, primaryName, birthYear, deathYear "
            "FROM name_basics WHERE nconst = 'nm0000001'",
            "name_basics 抽查（nm0000001）",
        ),
        (
            "SELECT tconst, directors, writers "
            "FROM title_crew WHERE tconst = 'tt0111161'",
            "title_crew 抽查",
        ),
        (
            "SELECT COUNT(*) FROM title_akas WHERE titleId = 'tt0111161'",
            "title_akas 肖申克别名单数",
        ),
    ]
    names = {table["name"] for table in selected}
    for sql, label in checks:
        if sql.split("FROM ")[1].split()[0] not in names:
            continue
        for row in conn.execute(sql):
            logger.info("%s -> %s", label, row)

    if "title_akas" in names:
        for row in conn.execute(
            "SELECT title, region, language FROM title_akas "
            "WHERE titleId = 'tt0111161' LIMIT 10"
        ):
            logger.info("title_akas 样例行 -> %s", row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tables",
        nargs="+",
        default=None,
        help="只导入指定表名（空格分隔），默认全部",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DATABASE_PATH,
        help="输出 SQLite 路径，默认项目根目录下的 imdb.db",
    )
    args = parser.parse_args()

    selected = [
        table
        for table in TABLES
        if args.tables is None or table["name"] in args.tables
    ]
    if not selected:
        raise SystemExit("没有匹配到任何表。")

    logger.info("数据库: %s", args.database)
    logger.info("待导入: %s", ", ".join(t["name"] for t in selected))

    conn = sqlite3.connect(args.database)
    configure_connection(conn)
    started = time.monotonic()
    try:
        for table in selected:
            import_table(conn, table)
            build_indexes(conn, table)
        verify(conn, selected)
    except KeyboardInterrupt:
        logger.warning("用户中断；已提交此前批次，可重跑续导。")
        raise
    finally:
        conn.close()

    logger.info("全部完成，总耗时 %.1f 分钟", (time.monotonic() - started) / 60)


if __name__ == "__main__":
    main()
