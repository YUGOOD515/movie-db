"""OpenAI 兼容的大模型接口，只用于识别难以清洗的作品名。

同时支持 ``/chat/completions`` 与 ``/responses`` 两种协议：
``LLM_API=chat``（默认）兼容绝大多数第三方服务；``LLM_API=responses``
用于 OpenAI Responses API 及兼容实现。只返回解析后的 JSON 对象。
"""

from __future__ import annotations

import json
import os
import re

import requests

from connectors import throttle

REQUEST_TIMEOUT = 60
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _settings() -> dict:
    api = os.environ.get("LLM_API", "chat").strip().lower()
    return {
        "base_url": (os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/"),
        "api_key": os.environ.get("LLM_API_KEY", "").strip(),
        "model": os.environ.get("LLM_MODEL", "").strip(),
        "api": "responses" if api == "responses" else "chat",
    }


def is_configured() -> bool:
    settings = _settings()
    return bool(settings["api_key"] and settings["model"])


def describe() -> dict:
    settings = _settings()
    return {"configured": is_configured(), "model": settings["model"], "api": settings["api"]}


def parse_json(text: str) -> dict:
    """解析模型输出；兼容 ```json 代码块和前后夹杂说明文字的情况。"""
    text = _FENCE.sub("", (text or "").strip())
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


def _post(settings: dict, endpoint: str, body: dict) -> requests.Response:
    # 免费额度按分钟 / 按天计：经 throttle 平滑请求，额度用尽时抛 RateLimitError 供调用方降级。
    return throttle.call("llm", lambda: requests.post(
        f"{settings['base_url']}{endpoint}", json=body, timeout=REQUEST_TIMEOUT,
        headers={"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"},
    ))


def complete_json(system: str, user: str) -> dict:
    settings = _settings()
    if not is_configured():
        raise RuntimeError("AI 识别尚未配置。")
    if settings["api"] == "responses":
        body = {"model": settings["model"], "instructions": system, "input": user,
                "text": {"format": {"type": "json_object"}}}
        response = _post(settings, "/responses", body)
        if response.status_code == 400:
            body = {key: value for key, value in body.items() if key != "text"}
            response = _post(settings, "/responses", body)
        response.raise_for_status()
        return parse_json(_responses_text(response.json()))
    body = {"model": settings["model"], "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    response = _post(settings, "/chat/completions", body)
    if response.status_code == 400:
        # 部分兼容服务不支持 response_format，退回纯提示约束。
        body = {key: value for key, value in body.items() if key != "response_format"}
        response = _post(settings, "/chat/completions", body)
    response.raise_for_status()
    choices = response.json().get("choices") or [{}]
    return parse_json(((choices[0].get("message") or {}).get("content")) or "")
