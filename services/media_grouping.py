"""按目录语义和文件片名划分作品；纯函数，不请求网盘或写库。"""

from collections import defaultdict
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import PurePosixPath
import re

from services.name_cleaning import folder_hints, normalize

VERSION = 1
_EPISODE = re.compile(r"(?i)(?:s\d{1,3}[ ._-]*e\d+|\b\d{1,2}x\d+|第[\d一二三四五六七八九十]+[集话話]|\bep?\d+)")
_STRUCTURAL = re.compile(
    r"(?i)^(?:s\d+|season[ ._-]*\d+|第[\d一二三四五六七八九十]+[季集话話]|"
    r"(?:4k|8k|\d{3,4}p|uhd|hdr|sdr|dv|remux|bluray|blu-ray|web-dl|webrip|h[ .]?26[45]|hevc)(?:\b|[-_\[]).*)$"
)
_GENERIC = {"video", "videos", "special", "series", "movies", "tv", "电影", "剧", "剧集", "合集", "来自分享"}
_MOVIE = re.compile(r"电影版|剧场版|劇場版|(?i:\bmovie\b)")


def structural(name):
    return bool(_STRUCTURAL.fullmatch(name.strip()))


@lru_cache(maxsize=4096)
def identity(name, *, filename=False):
    """片名线索；集号之后的单集名不参与作品划分，不猜测翻译或翻拍身份。"""
    source = PurePosixPath(name).stem if filename else name
    source = _EPISODE.split(source, maxsplit=1)[0].strip(" ._-")
    if not source or structural(source) or normalize(source) in _GENERIC:
        return ""
    hints = folder_hints(source)
    title = normalize(hints["queries"][0]) if hints["queries"] else ""
    return title if len(re.sub(r"[0-9]", "", title)) >= 2 else ""


def movie_edition(name):
    return bool(_MOVIE.search(name)) and not bool(_EPISODE.search(name))


def _movie_year(name):
    """不带集号的文件才看年份：同名电影（1984 / 2021 版《沙丘》）靠它区分，剧集不受影响。"""
    stem = PurePosixPath(name).stem
    return None if _EPISODE.search(stem) else folder_hints(stem)["year"]


def _title_keys(members):
    """同一目录内合并少量拼写差异，保留续集编号、国家版本和年份明显不同的同名电影。"""
    parsed = []
    for item in members:
        title = identity(item["name"], filename=True)
        parsed.append((title, _movie_year(item["name"]) if title else None))

    def same_title(title, key):
        return key == title or (
            len(title) >= 5 and title[:4] == key[:4]
            and re.findall(r"\d+", title) == re.findall(r"\d+", key)
            and SequenceMatcher(None, title, key).ratio() >= 0.88)

    # 先登记带年份的片名，没写年份的文件再并入同名的第一组，结果不受文件顺序影响。
    keys = []
    for title, year in sorted((value for value in parsed if value[0]), key=lambda value: value[1] is None):
        # 年份差一年按同一部处理：不同来源对上映年份常有出入。
        if not any(same_title(title, key) and (year is None or known is None or abs(year - known) <= 1)
                   for key, known in keys):
            keys.append((title, year))
    found = []
    for title, year in parsed:
        key = next(((key, known) for key, known in keys if same_title(title, key)
                    and (year is None or known is None or abs(year - known) <= 1)), None) if title else None
        found.append("" if key is None else key[0] if key[1] is None else f"{key[0]} ({key[1]})")
    return found


def partition(path, files):
    """先找最近的作品目录，再拆分同目录中明确不同的片名/电影版。每个文件恰好出现一次。"""
    root = PurePosixPath(path)
    anchors = defaultdict(list)
    for item in sorted(files, key=lambda value: value["path"]):
        source = PurePosixPath(item["path"])
        parent = source.parent
        while parent != root and root in parent.parents and structural(parent.name):
            parent = parent.parent
        anchors[str(parent)].append(item)

    groups = []
    for anchor, members in sorted(anchors.items()):
        titles = defaultdict(list)
        for item, key in zip(members, _title_keys(members)):
            # 同名电影版与剧集的身份不同；即使文件名含有 S01，也不能混入正片剧集。
            titles[(key, movie_edition(item["name"]))].append(item)
        meaningful = {key for key, edition in titles if key}
        split = len(meaningful) > 1 or len({edition for _, edition in titles}) > 1
        subsets = list(titles.values()) if split else [members]
        for subset in subsets:
            # 拆开的子集用实际文件路径作稳定标识，避免虚构目录和重名记录。
            group_path = subset[0]["path"] if split else anchor
            name = subset[0]["name"] if split else PurePosixPath(anchor).name
            stamp = max(subset, key=lambda value: value.get("sort_time", 0))
            groups.append({**stamp, "path": group_path, "name": name, "kind": "file" if len(subset) == 1 and split else "folder",
                           "files": subset, "suggestion": None,
                           "context_path": anchor, "grouping_version": VERSION})
    return groups


def samples(record, limit=8):
    """分层采样：先覆盖不同子目录和片名，再补首尾集；返回相对路径而非只有文件名。"""
    base = PurePosixPath(record.get("context_path") or record["path"])
    if base.suffix:
        base = base.parent
    buckets = defaultdict(list)
    for item in sorted(record["files"], key=lambda value: value["path"]):
        path = PurePosixPath(item["path"])
        relative = path.relative_to(base) if path.is_relative_to(base) else PurePosixPath(item["name"])
        buckets[(str(relative.parent), identity(item["name"], filename=True))].append(str(relative))
    ordered = list(buckets.values())
    selected = [values[0] for values in ordered][:limit]
    for values in ordered:
        if len(selected) >= limit:
            break
        if values[-1] not in selected:
            selected.append(values[-1])
    return selected


def conflicts(record):
    """编号相同并不证明属于同一作品；明确不同片名的组不能直接确认。"""
    titles = set(_title_keys(record["files"]))
    titles.discard("")
    return len(titles) > 1
