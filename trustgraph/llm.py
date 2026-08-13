"""Minimal OpenAI-compatible chat client for the extraction LLM.

Provider-agnostic: any endpoint exposing POST {base}/chat/completions works.
Defaults target Moonshot/Kimi's international endpoint; users in China set
LLM_BASE_URL=https://api.moonshot.cn/v1.

Env: LLM_BASE_URL, LLM_MODEL (default kimi-k2), LLM_API_KEY (required).
No network calls happen at import time; tests exercise only extract_json.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


def _config() -> dict:
    return {
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.moonshot.ai/v1"),
        "model": os.environ.get("LLM_MODEL", "kimi-k2"),
        "api_key": os.environ.get("LLM_API_KEY", ""),
    }


def extract_json(text: str) -> Any:
    """Pull the first complete JSON value out of an LLM response.

    Tolerates markdown ```json fences and surrounding prose; raises LLMError
    when nothing parseable is present.
    """
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", candidate):
        try:
            value, _ = decoder.raw_decode(candidate, match.start())
            return value
        except json.JSONDecodeError:
            continue
    raise LLMError(f"no parseable JSON in LLM response: {text[:200]!r}")


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: float = 60.0,
) -> str:
    cfg = _config()
    if not cfg["api_key"]:
        raise LLMError("LLM_API_KEY is not set — export it before running extraction")
    payload: dict[str, Any] = {
        "model": model or cfg["model"],
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"

    last_error: LLMError | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code >= 500:
                last_error = LLMError(
                    f"LLM server error (HTTP {resp.status_code}): {resp.text[:300]}"
                )
            elif resp.status_code != 200:
                raise LLMError(
                    f"LLM request failed (HTTP {resp.status_code}): {resp.text[:300]}"
                )
            else:
                return resp.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            last_error = LLMError(f"LLM network error: {exc}")
        time.sleep(1.5 * (attempt + 1))
    raise last_error or LLMError("LLM request failed without a recorded error")


def chat_json(messages: list[dict], **kwargs: Any) -> Any:
    return extract_json(chat(messages, **kwargs))
