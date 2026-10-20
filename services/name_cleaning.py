"""从网盘目录名与文件名中提取可搜索的作品名、年份与季集位置。

网盘分享常把片名故意写乱以躲避审查：首字母前缀（``X 芳``）、插入干扰符
（``鱿.鱼.游.戏``、``挽z救z计z划``、``破か地か獄``）、再叠加画质与字幕说明。
这里只做确定性的清洗；同音字、形近字、拆字等需要语义理解的情况交给 AI。
全部为纯函数，不访问网络或数据库。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import PurePosixPath

from guessit import guessit

CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
KANA = "\u3040-\u30ff"
CHINESE_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 画质、版本、字幕、体积等发布说明，出现在片名之外。
NOISE = re.compile(
    r"(?i)(?:"
    r"(?<![a-z0-9])(?:4k|8k|2160p|1080p|1080i|720p|uhd|hdr10\+?|hdr|sdr|dv|dovi|remux|bluray|blu-ray|web-?dl|webrip|web|"
    r"hevc|h\.?26[45]|x26[45]|avc|aac|dts(?:-hd)?|truehd|atmos|ddp?\d\.\d|10bit|\d+fps|sup|proper)(?![a-z0-9])"
    r"|杜比视界|杜比全景声|蓝光|原盘|高码率?|\d+帧|(?:中英|中文|双语|简繁|简体|繁体|英文|特效|内嵌|内封|外挂|[a-z&]+)?字幕|中字|双字|中英双?字?|简英\S*|简中\S*|繁中\S*|国英\S*|国粤\S*"
    r"|内封\S*|内嵌\S*|外挂\S*|精修\S*|特效\S*|官译\S*|\d*音轨|\d+audios?"
    r"|年糕封装|封装|二合一|全\d+[集部季]|\d+\s*-\s*\d+\s*季全?集?|\d+季全|全\d*季?集|全系列|\d+集全|单集\S*"
    r"|豆瓣\s*[\d.]+分?|(?<![a-z0-9.])\d+(?:\.\d+)?\s*[GT]B?(?![a-z0-9])|英语|国语|粤语|韩语|日语|中英"
    r")"
)
TAGS = re.compile(r"(?i)^(?:bl剧|高清|合集|完结|更新中?|蓝光|4k|全集|中字|国产剧|美剧|韩剧|日剧|英剧|电影|剧集|纪录片|动漫)$")
# 汉字之间明显的干扰符；“：”“·”这类可能属于正式片名的符号只在重复出现时才视为干扰。
HARD_SEPARATORS = "丨|@#~+*_/\\"
SOFT_SEPARATORS = r".\s·•\-&、z"
LEADING_LABEL = re.compile(rf"^(?:[A-Z]|\d{{1,3}})\s*[-_.、]?\s*(?=[《【\[{CJK}])")
YEAR = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9]|p|P)")
SEASON_HINT = re.compile(r"(?i)(?:第\s*([0-9一二三四五六七八九十]{1,3})\s*季|season\s*([0-9]{1,3})|\bS([0-9]{1,2})\b(?!\s*[-~]))")


def _number(value: str | None) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + CHINESE_NUMBERS.get(value[1:], 0)
    if value.endswith("十"):
        return CHINESE_NUMBERS.get(value[0], 0) * 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        return CHINESE_NUMBERS.get(tens, 0) * 10 + CHINESE_NUMBERS.get(ones, 0)
    return CHINESE_NUMBERS.get(value)


def normalize(value: str) -> str:
    """比较用的规范形式：全角转半角、忽略大小写、去掉标点与空白。"""
    value = unicodedata.normalize("NFKC", value or "").casefold().replace("&", "and")
    return re.sub(rf"[^0-9a-z{CJK}{KANA}\uac00-\ud7af]", "", value)


def _remove_interference(text: str) -> str:
    # 硬干扰符：汉字之间出现一次即删除。
    text = re.sub(rf"(?<=[{CJK}])[{re.escape(HARD_SEPARATORS)}]+(?=[{CJK}])", "", text)
    # 软干扰符与假名：同一种分隔在汉字之间重复出现时才删除，保留“破·地狱”这类正式写法。
    pattern = re.compile(rf"(?<=[{CJK}])([{SOFT_SEPARATORS}]+|[{KANA}])(?=[{CJK}])")
    counts = Counter(match.group(1) for match in pattern.finditer(text))
    repeated = {separator for separator, count in counts.items() if count >= 2}
    if repeated:
        text = pattern.sub(lambda match: "" if match.group(1) in repeated else match.group(0), text)
    # 正式中文片名几乎不会在两个汉字之间用半角句点，出现一次也视为干扰。
    text = re.sub(rf"(?<=[{CJK}])\.(?=[{CJK}])", "", text)
    # “现..zai..就”：干扰符夹着拼音时先去掉符号，拼音交给 AI。
    return re.sub(rf"(?<=[{CJK}])\.{{2,}}|\.{{2,}}(?=[{CJK}])", "", text)


def _strip_noise(text: str) -> str:
    text = NOISE.sub(" ", text)
    text = re.sub(r"[(（\[]\s*[)）\]]", " ", text)
    text = re.sub(r"\(\s*\d\s*\)$", " ", text.strip())          # “(1)”“(2)”副本编号
    text = re.sub(r"\s*第\s*[0-9一二三四五六七八九十]{1,3}\s*季\s*", " ", text)
    text = re.sub(r"(?i)\bseason\s*\d+\b", " ", text)
    text = re.sub(r"[&+]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .-_·:：,，、")


def _bracketed(text: str) -> list[tuple[str, str]]:
    return re.findall(r"([《【\[])([^》】\]]*)[》】\]]", text)


def folder_hints(name: str) -> dict:
    """目录名 → {"queries": [...], "year": int|None, "season": int|None}。

    queries 按可信度排列，第一项最可能是片名；去掉首字母前缀后的写法和保留
    前缀的写法同时给出（“X战警”这类片名本身以字母开头）。
    """
    raw = unicodedata.normalize("NFKC", name or "").strip()
    raw = re.sub(r"(?i)\.(mkv|mp4|avi|ts|m2ts|mov|wmv|flv|webm|m4v|mpg|mpeg|rmvb)$", "", raw)
    year_match = YEAR.search(raw)
    season_match = SEASON_HINT.search(raw)
    season = next((_number(group) for group in (season_match.groups() if season_match else ()) if group), None)

    def title_from(text: str) -> str:
        brackets = _bracketed(text)
        # 《》通常直接包住片名。
        for kind, content in brackets:
            if kind == "《" and _strip_noise(_remove_interference(content)):
                return content
        # 以【】或 [] 开头时，第一个不是标签的括号内容即片名。
        if brackets and re.match(r"^[【\[]", text):
            for _, content in brackets:
                cleaned = _strip_noise(YEAR.sub(" ", _remove_interference(content)))
                if cleaned and not TAGS.match(cleaned):
                    return content
        outside = re.sub(r"[【\[][^】\]]*[】\]]", " ", text)
        outside = re.sub(r"[《》]", " ", outside)
        return outside

    variants = []
    for text in (LEADING_LABEL.sub("", raw), raw):
        title = title_from(text)
        title = re.sub(r"[(（\[]?\s*(?:19|20)\d{2}(?:\s*-\s*(?:19|20)\d{2})?\s*[)）\]]?", " ", title)
        title = _strip_noise(_remove_interference(title))
        title = _remove_interference(title)
        # 名称后面紧跟的规格说明（“同乐者4K英语”）已被删除，再去掉残留的孤立数字。
        title = re.sub(r"\s+\d{1,2}$", "", title).strip()
        if title:
            variants.append(title)
            without_note = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", title).strip()
            if without_note and without_note != title:
                variants.append(without_note)
    # 纯英文的点号命名（White.Snake.2019.1080p）直接交给 guessit。
    if not re.search(rf"[{CJK}{KANA}]", raw):
        try:
            guessed = guessit(raw).get("title")
        except Exception:
            guessed = None
        if isinstance(guessed, str) and re.search(r"[A-Za-z]{3,}", guessed) and not JUNK_TITLES.match(guessed):
            variants.insert(0, guessed)
    queries = list(dict.fromkeys(variant for variant in variants if normalize(variant)))
    return {
        "queries": queries,
        "year": int(year_match.group(1)) if year_match else None,
        "season": season,
    }


JUNK_TITLES = re.compile(
    r"(?i)^(?:s\d+|e\d+|\d+|4k|8k|web|web-dl|dl|hifi|it|nf|atvp|amzn|dsnp|hmax|pcok|uhd|hdr|sdr|dv|"
    r"remux|bluray|audio|audios|\d+audios?|h265|h264|hevc|aac|proper|repack|complete|part \d+|new)$"
)


def english_title(paths: list[str]) -> str | None:
    """文件名里常有干净的英文片名（目录名再乱也一样），取出现最多的一个。"""
    titles = Counter()
    for path in paths[:12]:
        stem = PurePosixPath(path).name
        try:
            title = guessit(stem).get("title")
        except Exception:
            continue
        if not isinstance(title, str):
            continue
        title = re.sub(r"^(?i:new)-", "", title).strip(" -.")
        if re.search(rf"[{CJK}{KANA}]", title) or not re.search(r"[A-Za-z]{3,}", title):
            continue
        if JUNK_TITLES.match(title):
            continue
        titles[title] += 1
    return titles.most_common(1)[0][0] if titles else None


EPISODE_PATTERNS = (
    re.compile(r"(?i)(?<![a-z0-9])s(\d{1,3})[ ._-]*e(\d{1,4})(?!\d)"),
    re.compile(r"(?i)(?<![a-z0-9])s(\d{1,3})[ ._-](\d{1,4})(?![\dp])"),
    re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{1,4})(?!\d)"),
    re.compile(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*季\s*第\s*([0-9一二三四五六七八九十]{1,4})\s*[集话話]"),
)
EPISODE_ONLY = (
    re.compile(r"第\s*([0-9一二三四五六七八九十]{1,4})\s*[集话話期]"),
    re.compile(r"(?i)(?<![a-z])ep?\s*\.?\s*(\d{1,4})(?![\dp])"),
    re.compile(r"^\[?(\d{1,4})\]?(?![\dkKpP年月日:.\-]\d|\d|[kKpP])"),
    re.compile(r"(?<![\dA-Za-z])[\[【](\d{1,4})[\]】]"),
)


def has_episode_marker(name: str) -> bool:
    stem = PurePosixPath(name).stem
    return any(pattern.search(stem) for pattern in EPISODE_PATTERNS) or any(
        pattern.search(stem) for pattern in EPISODE_ONLY[:2]
    )


def guess_position(path: str, *, season_hint: int | None = None) -> tuple[int | None, int | None]:
    """单个视频文件 → (季号, 集号)；无法判断的部分为 None。"""
    source = PurePosixPath(path)
    stem = unicodedata.normalize("NFKC", source.stem)
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(stem)
        if match:
            return _number(match.group(1)), _number(match.group(2))
    episode = None
    for pattern in EPISODE_ONLY:
        match = pattern.search(stem)
        if match:
            value = _number(match.group(1))
            if value is not None and not (1900 <= value <= 2099):
                episode = value
                break
    season = None
    for parent in source.parents:
        match = SEASON_HINT.search(unicodedata.normalize("NFKC", parent.name))
        if match:
            season = next((_number(group) for group in match.groups() if group), None)
            break
    if season is None:
        season = season_hint
    if episode is None:
        try:
            parsed = guessit(source.name)
        except Exception:
            parsed = {}
        value = parsed.get("episode")
        if isinstance(value, int):
            episode = value
            if season is None and isinstance(parsed.get("season"), int):
                season = parsed["season"]
    return season, episode
