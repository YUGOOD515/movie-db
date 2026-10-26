"""OpenAI 兼容的大模型接口，只用于识别难以清洗的作品名。

同时支持 ``/responses`` 与 ``/chat/completions`` 两种协议，只返回解析后的 JSON 对象。

DeepSeek（``api.deepseek.com``）按官方文档专门处理：
- 默认走 Responses API（``instructions`` + ``input``，``text.format`` 要求 JSON 对象）；
- 关闭思考模式（Responses 用 ``reasoning.effort = none``，Chat 用 ``thinking.type = disabled``）：
  识别片名是分类任务，思考只会增加延迟和输出 token；
- 不按每分钟次数限速：官方只限账号并发（deepseek-flash 2500、deepseek-v4-pro 500），
  这里默认同时 64 个请求（遇到 429 自动减半、之后逐步恢复），由 ``LLM_CONCURRENCY`` 调整；
  每批默认 8 条：小批次让并发真正铺开，单次响应也更快，由 ``LLM_BATCH_SIZE`` 调整；
- 非流式请求在排队时会持续返回空行保活，读超时按“两次收到数据的间隔”计算，不会误判。

智谱（``open.bigmodel.cn``）的 GLM 默认开启思考，Chat 请求同样发送 ``thinking.type = disabled``。

其它平台沿用通用参数（不发送平台私有字段），默认 Chat Completions，``LLM_API`` 可覆盖。
"""

from __future__ import annotations

import json
import os
import re
from urllib.parse import urlparse

import requests

from connectors import throttle

# (连接, 读取) 超时：读取超时是两次收到数据之间的间隔，DeepSeek 排队保活的空行会重置它。
REQUEST_TIMEOUT = (10, 120)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_THINKING = ("none", "low", "high", "max")


class LlmError(requests.RequestException):
    """AI 接口返回了明确的错误。fatal=True 表示重试无用（Key 无效、余额不足），本轮应停止调用。"""

    def __init__(self, message: str, *, status: int | None = None, fatal: bool = False, retry: bool = False):
        super().__init__(message)
        self.status = status
        self.fatal = fatal
        self.retry = retry


def provider(base_url: str) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if host == "api.deepseek.com" or host.endswith(".deepseek.com"):
        return "deepseek"
    if host == "bigmodel.cn" or host.endswith(".bigmodel.cn"):
        return "zhipu"
    return "openai-compatible"


def _settings(overrides: dict | None = None) -> dict:
    env = {**os.environ, **{key: value for key, value in (overrides or {}).items() if value is not None}}
    base_url = (env.get("LLM_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")
    kind = provider(base_url)
    if kind == "deepseek":
        # 官方 base_url 是 https://api.deepseek.com；旧配置里的 /v1 前缀统一去掉。
        base_url = re.sub(r"/v1$", "", base_url)
    api = env.get("LLM_API", "").strip().lower()
    if api not in ("chat", "responses"):
        api = "responses" if kind == "deepseek" else "chat"
    thinking = env.get("LLM_THINKING", "").strip().lower() or "none"
    return {
        "base_url": base_url,
        "api_key": env.get("LLM_API_KEY", "").strip(),
        "model": env.get("LLM_MODEL", "").strip(),
        "api": api,
        "provider": kind,
        "thinking": thinking if thinking in _THINKING else "none",
    }


def is_configured() -> bool:
    settings = _settings()
    return bool(settings["api_key"] and settings["model"])


def describe() -> dict:
    settings = _settings()
    return {"configured": is_configured(), "model": settings["model"], "api": settings["api"],
            "provider": settings["provider"], "concurrency": concurrency(), "batch_size": batch_size()}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return max(low, min(high, value))


def concurrency() -> int:
    """同时在途的 AI 请求数，与 throttle 的 llm 策略一致。"""
    return throttle.limiter("llm").policy.concurrency


def batch_size() -> int:
    """合并请求时每次最多带几条记录：DeepSeek 并发宽裕，默认 8 条一批；其他平台按次数限速，默认 20。"""
    default = 8 if _settings()["provider"] == "deepseek" else 20
    return _env_int("LLM_BATCH_SIZE", default, 1, 50)


def parse_json(text: str) -> dict:
    """解析模型输出；兼容 ```json 代码块和前后夹杂说明文字的情况。"""
    text = _FENCE.sub("", (text or "").strip())
    if not text:
        raise ValueError("AI 返回了空内容。")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("AI 未返回 JSON。") from None
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI 返回的 JSON 不是对象。")
    return value


def _responses_text(payload: dict) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in ("output_text", "text") and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "".join(parts)


def _error_message(response) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "")[:200]
    return str(error or "")[:200]


def _raise_for_status(response) -> None:
    """按 DeepSeek / OpenAI 的错误码给出可操作的说明。"""
    status = response.status_code
    if status < 400:
        return
    detail = _error_message(response)
    suffix = f"（{detail}）" if detail else ""
    if status in (401, 403):
        raise LlmError("AI 接口 API Key 无效或没有权限。", status=status, fatal=True)
    if status == 402:
        raise LlmError("AI 账号余额不足，请充值后重新识别。", status=status, fatal=True)
    if status == 404:
        raise LlmError(f"AI 接口地址或模型名称不存在{suffix}。", status=status, fatal=True)
    if status in (400, 422):
        raise LlmError(f"AI 接口不接受请求参数{suffix}。", status=status)
    if status == 429:
        raise LlmError("AI 接口请求过于频繁或额度已用尽，请稍后再试。", status=status, retry=True)
    if status >= 500:
        raise LlmError(f"AI 服务暂时不可用（HTTP {status}），稍后重新扫描会重试。", status=status, retry=True)
    raise LlmError(f"AI 接口返回错误（HTTP {status}）{suffix}。", status=status)


def _request(settings: dict, endpoint: str, body: dict, timeout=REQUEST_TIMEOUT) -> requests.Response:
    return requests.post(f"{settings['base_url']}{endpoint}", json=body, timeout=timeout,
                         headers={"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"})


def _post(settings: dict, endpoint: str, body: dict) -> requests.Response:
    # 经 throttle 控制并发 / 速率；429、503 按 Retry-After 退避，额度用尽时抛 RateLimitError 供调用方降级。
    if settings.get("probe"):
        return _request(settings, endpoint, body, timeout=(10, 30))
    return throttle.call("llm", lambda: _request(settings, endpoint, body))


def _add_usage(usage: dict | None, raw: dict | None) -> None:
    """累计 token 用量；兼容 Responses（input/output_tokens）与 Chat（prompt/completion_tokens）。"""
    if usage is None or not isinstance(raw, dict):
        return
    cached = (raw.get("input_tokens_details") or {}).get("cached_tokens")
    if cached is None:
        cached = raw.get("prompt_cache_hit_tokens", (raw.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    values = {
        "requests": 1,
        "input_tokens": raw.get("input_tokens", raw.get("prompt_tokens", 0)) or 0,
        "cached_tokens": cached or 0,
        "output_tokens": raw.get("output_tokens", raw.get("completion_tokens", 0)) or 0,
    }
    for key, value in values.items():
        usage[key] = usage.get(key, 0) + int(value)


def _responses_body(settings: dict, system: str, user: str, max_tokens: int | None) -> dict:
    body = {"model": settings["model"], "instructions": system, "input": user,
            "text": {"format": {"type": "json_object"}}}
    if max_tokens:
        body["max_output_tokens"] = max_tokens
    if settings["provider"] == "deepseek":
        body["reasoning"] = {"effort": settings["thinking"]}
    return body


def _chat_body(settings: dict, system: str, user: str, max_tokens: int | None) -> dict:
    body = {"model": settings["model"], "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if max_tokens:
        body["max_tokens"] = max_tokens
    if settings["provider"] == "deepseek":
        if settings["thinking"] == "none":
            body["thinking"] = {"type": "disabled"}
        else:
            body["thinking"], body["reasoning_effort"] = {"type": "enabled"}, settings["thinking"]
    elif settings["provider"] == "zhipu":
        body["thinking"] = {"type": "disabled" if settings["thinking"] == "none" else "enabled"}
    return body


def _send(settings: dict, system: str, user: str, max_tokens: int | None, usage: dict | None) -> str:
    responses = settings["api"] == "responses"
    endpoint = "/responses" if responses else "/chat/completions"
    body = (_responses_body if responses else _chat_body)(settings, system, user, max_tokens)
    response = _post(settings, endpoint, body)
    if response.status_code == 400 and settings["provider"] != "deepseek":
        # 部分兼容服务不支持 JSON 模式，退回纯提示约束。
        body = {key: value for key, value in body.items() if key not in ("text", "response_format")}
        response = _post(settings, endpoint, body)
    _raise_for_status(response)
    try:
        payload = response.json()
    except ValueError as error:
        raise LlmError("AI 接口返回了无法解析的内容。", status=response.status_code, retry=True) from error
    _add_usage(usage, payload.get("usage"))
    if responses:
        if payload.get("status") == "incomplete":
            raise LlmError("AI 输出被截断（达到输出 token 上限）。", retry=True)
        if payload.get("status") == "failed":
            raise LlmError(f"AI 生成失败：{(payload.get('error') or {}).get('message') or '未知原因'}。", retry=True)
        return _responses_text(payload)
    choice = (payload.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise LlmError("AI 输出被截断（达到输出 token 上限）。", retry=True)
    return (choice.get("message") or {}).get("content") or ""


def complete_json(system: str, user: str, *, max_tokens: int | None = None, usage: dict | None = None) -> dict:
    """发送一次请求并返回 JSON 对象。

    固定的 system 放在最前（Responses 的 instructions 会作为第一条 system 消息），批量请求共享同一前缀，
    可以命中 DeepSeek 的上下文硬盘缓存。JSON 模式偶尔返回空内容，空内容时原样重试一次。
    """
    settings = _settings()
    if not is_configured():
        raise RuntimeError("AI 识别尚未配置。")
    text = _send(settings, system, user, max_tokens, usage)
    if not text.strip():
        text = _send(settings, system, user, max_tokens, usage)
    return parse_json(text)


def probe(base_url: str, api_key: str, model: str) -> dict:
    """设置页“测试连接”：用表单里的值真实发一次极小的 JSON 请求（不经限速队列）。

    返回 {"api", "provider", "elapsed", "usage"}；失败抛 LlmError / requests.RequestException / ValueError。
    """
    import time

    settings = {**_settings({"LLM_BASE_URL": base_url, "LLM_API_KEY": api_key, "LLM_MODEL": model}), "probe": True}
    usage: dict = {}
    started = time.monotonic()
    limit = 50 if settings["provider"] == "deepseek" else None  # 其它平台可能默认开启思考，不设上限以免截断
    text = _send(settings, '只输出 JSON，例如 {"ok": true}。', '测试连接，请回复 json：{"ok": true}', limit, usage)
    answer = parse_json(text)
    return {"api": settings["api"], "provider": settings["provider"], "ok": answer.get("ok") is True,
            "elapsed": round(time.monotonic() - started, 1), "usage": usage}
