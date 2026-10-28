"""豆瓣评分（非官方接口，可能随时失效）。

豆瓣没有对外开放的 API，这里沿用 MoviePilot 等开源项目使用的豆瓣 App 接口，实测（2025）：

- IMDb ID → 豆瓣 ID：先用 Wikidata（属性 P4529“豆瓣电影 ID”，正规、免费，可批量查询），
  查不到再用旧版接口 ``api.douban.com/v2/movie/imdb/{id}``。不按片名搜索：App 搜索接口
  在国内 IP 上要求登录，按名称猜测也容易配错。
- 评分：豆瓣 App 详情接口 ``frodo.douban.com/api/v2/{movie|tv}/{id}``（HMAC-SHA1 签名）。
  网页版会跳转到人机验证，不使用。

请求统一走 throttle 的 "douban" 策略（约 2 秒一次、不并发）。遇到 403 / 需要登录 / 跳转人机验证，
整个豆瓣服务暂停 30 分钟，期间直接返回失败，调用方保留库内旧值。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import random
import re
from datetime import datetime
from urllib.parse import quote, urlparse

import requests

from connectors import throttle


logger = logging.getLogger(__name__)

FRODO_BASE = "https://frodo.douban.com/api/v2"
FRODO_KEY = "0dad551ec0f84ed02907ff5c42e8ec70"
FRODO_SECRET = "bf7dddc7c9cfe6f7"
V2_BASE = "https://api.douban.com/v2"
V2_KEY = "0ab215a8b1977939201640fa14c66bab"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_BATCH = 100
APP_UAS = (
    "api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 vendor/HUAWEI model/Mate 40 "
    "brand/HUAWEI  rom/android  network/wifi  platform/AndroidPad",
    "api-client/1 com.douban.frodo/7.18.0(230) Android/22 product/MI 9 vendor/Xiaomi model/MI 9 brand/Android  "
    "rom/miui6  network/wifi  platform/mobile nd/1",
)
WIKIDATA_UA = "MovieDB/1.0 (https://github.com/YUGOOD515/movie-db) python-requests"
TIMEOUT = (6, 15)
BLOCK_PAUSE = 30 * 60
DOUBAN_ID = re.compile(r"^\d{1,12}$")
IMDB_ID = re.compile(r"^tt\d{5,12}$")


class DoubanBlocked(throttle.RateLimitError):
    """豆瓣拒绝访问（403、需要登录或人机验证）：暂停一段时间。"""

    def __init__(self, reason: str):
        super().__init__("douban", BLOCK_PAUSE)
        self.reason = reason


def enabled() -> bool:
    return os.environ.get("DOUBAN_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _blocked(reason: str):
    throttle.limiter("douban").pause(BLOCK_PAUSE, reason="blocked")
    logger.warning("豆瓣拒绝访问（%s），暂停约 30 分钟", reason)
    raise DoubanBlocked(reason)


def _check(response: requests.Response) -> dict:
    """解析豆瓣接口的响应；被拦截时暂停整个服务并抛出 DoubanBlocked。"""
    location = response.headers.get("Location", "") if response.is_redirect else ""
    if response.status_code in (401, 403) or "sec.douban.com" in location or "accounts.douban.com" in location:
        _blocked(f"HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        if response.status_code == 404:
            return {}
        raise requests.RequestException(f"豆瓣返回的不是 JSON（HTTP {response.status_code}）")
    if isinstance(data, dict):
        message = str(data.get("msg") or data.get("localized_message") or "")
        if "need_login" in message or data.get("code") in (103, 112, 1309):
            _blocked(message or str(data.get("code")))
    if response.status_code == 404:
        return {}
    if response.status_code >= 400:
        raise requests.RequestException(f"豆瓣返回错误（HTTP {response.status_code}）")
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------- IMDb ID → 豆瓣 ID


def wikidata_ids(imdb_ids: list[str]) -> dict[str, str]:
    """Wikidata 批量查豆瓣 ID；查询失败的批次跳过（返回已查到的部分）。"""
    wanted = [value for value in dict.fromkeys(imdb_ids) if IMDB_ID.match(value)]
    found: dict[str, str] = {}
    for start in range(0, len(wanted), WIKIDATA_BATCH):
        batch = wanted[start:start + WIKIDATA_BATCH]
        values = " ".join(f'"{value}"' for value in batch)
        query = f"SELECT ?imdb ?douban WHERE {{ VALUES ?imdb {{ {values} }} ?item wdt:P345 ?imdb. ?item wdt:P4529 ?douban. }}"
        try:
            response = throttle.call("wikidata", lambda: requests.get(
                WIKIDATA_SPARQL, params={"query": query, "format": "json"},
                headers={"User-Agent": WIKIDATA_UA, "Accept": "application/sparql-results+json"}, timeout=(6, 60)))
            response.raise_for_status()
            rows = response.json()["results"]["bindings"]
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            logger.info("Wikidata 查询豆瓣 ID 失败：%s", error)
            continue
        for row in rows:
            imdb_id, douban_id = row["imdb"]["value"], row["douban"]["value"]
            if DOUBAN_ID.match(douban_id):
                found.setdefault(imdb_id, douban_id)
    return found


def v2_id(imdb_id: str) -> str | None:
    """旧版接口按 IMDb ID 查豆瓣 ID；查不到返回 None。"""
    data = throttle.call("douban", lambda: requests.post(
        f"{V2_BASE}/movie/imdb/{imdb_id}", data={"apikey": V2_KEY},
        headers={"User-Agent": random.choice(APP_UAS),
                 "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
        timeout=TIMEOUT, allow_redirects=False), consume=_check)
    match = re.search(r"(\d+)/?$", str(data.get("id") or data.get("alt") or ""))
    return match.group(1) if match else None


# ---------------------------------------------------------------- 评分


def _sign(url: str, ts: str) -> str:
    raw = "&".join(["GET", quote(urlparse(url).path, safe=""), ts])
    return base64.b64encode(hmac.new(FRODO_SECRET.encode(), raw.encode(), hashlib.sha1).digest()).decode()


def _frodo(path: str) -> dict:
    url = FRODO_BASE + path
    ts = datetime.now().strftime("%Y%m%d")
    params = {"apiKey": FRODO_KEY, "os_rom": "android", "_ts": ts, "_sig": _sign(url, ts)}
    return throttle.call("douban", lambda: requests.get(
        url, params=params, headers={"User-Agent": random.choice(APP_UAS)}, timeout=TIMEOUT, allow_redirects=False),
        consume=_check)


def rating(douban_id: str, title_type: str) -> tuple[float | None, int | None] | None:
    """返回 (评分, 人数)；条目不存在返回 None；“暂无评分”返回 (None, None)。"""
    order = ("tv", "movie") if title_type == "series" else ("movie", "tv")
    for kind in order:
        data = _frodo(f"/{kind}/{douban_id}")
        if not data.get("id"):
            continue
        info = data.get("rating") or {}
        try:
            value, count = float(info.get("value") or 0), int(info.get("count") or 0)
        except (TypeError, ValueError):
            value, count = 0.0, 0
        # 评价人数不足时豆瓣返回 0 分
        return (round(value, 1), count) if value > 0 else (None, None)
    return None


def lookup(imdb_id: str, title_type: str, douban_id: str | None = None, *, wikidata: bool = True) -> dict:
    """单部作品：查豆瓣 ID（已知则跳过）并取评分。返回要写入 title_basic 的字段，查不到返回空字典。

    wikidata=False 表示调用方已批量查过 Wikidata，直接用旧版接口兜底。
    被豆瓣拦截时抛出 DoubanBlocked（RateLimitError）；网络错误照常抛出 requests 异常。
    """
    from models import api_usage
    key = f"douban:v1:{imdb_id}:{title_type}"
    cached = api_usage.cached(key)
    if cached is not None and (not douban_id or cached.get("douban_id") == douban_id):
        return cached
    fields = _lookup(imdb_id, title_type, douban_id, wikidata=wikidata)
    # 同一剧集分批导入时复用评分；服务错误不缓存，未找到仅缓存一小时。
    api_usage.remember(key, fields, 21600 if fields else 3600)
    return fields


def _lookup(imdb_id: str, title_type: str, douban_id: str | None, *, wikidata: bool) -> dict:
    if not douban_id and wikidata:
        douban_id = wikidata_ids([imdb_id]).get(imdb_id)
    if not douban_id:
        douban_id = v2_id(imdb_id)
    if not douban_id:
        return {}
    result = rating(douban_id, title_type)
    if result is None:
        return {}
    fields: dict = {"douban_id": douban_id}
    if result[0] is not None:
        fields.update(douban_rating=result[0], douban_votes=result[1])
    return fields
