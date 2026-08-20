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
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


def _config() -> dict:
    timeout = os.environ.get("LLM_TIMEOUT")
    return {
        "base_url": os.environ.get("LLM_BASE_URL", "https://api.moonshot.ai/v1"),
        "model": os.environ.get("LLM_MODEL", "kimi-k2"),
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "timeout": float(timeout) if timeout else 600.0,
    }


def extract_json(text: str) -> Any:
    """Pull the first complete JSON value out of an LLM response.

    Tolerates markdown ```json fences and surrounding prose; raises LLMError
    when nothing parseable is present.
    """
    if not isinstance(text, str):
        raise LLMError(
            f"LLM response content must be a string, got {type(text).__name__}"
        )
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fence.group(1) if fence else text
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\{\[]", candidate):
        try:
            value, _ = decoder.raw_decode(candidate, match.start())
            return value
        except json.JSONDecodeError:
            continue
    raise LLMError("no parseable JSON in LLM response")


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    timeout: float | None = None,
) -> str:
    try:
        cfg = _config()
    except (TypeError, ValueError) as exc:
        raise LLMError("LLM configuration is invalid") from exc
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
    request_timeout = timeout or cfg["timeout"]

    context = (
        f"endpoint={url!r} model={payload['model']!r} message_count={len(messages)}"
    )
    try:
        with httpx.Client(timeout=request_timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 500:
            raise LLMError(f"LLM server error HTTP {resp.status_code} ({context})")
        if resp.status_code != 200:
            raise LLMError(f"LLM request failed HTTP {resp.status_code} ({context})")
        try:
            response_json = resp.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise LLMError(f"LLM invalid JSON response ({context})") from exc
        if not isinstance(response_json, dict):
            raise LLMError(
                f"LLM malformed response: root must be an object ({context})"
            )
        choices = response_json.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError(
                f"LLM malformed response: choices must be non-empty ({context})"
            )
        first = choices[0]
        if not isinstance(first, dict) or not isinstance(first.get("message"), dict):
            raise LLMError(f"LLM malformed response: message is missing ({context})")
        content = first["message"].get("content")
        if not isinstance(content, str):
            raise LLMError(
                f"LLM malformed response: content is not a string ({context})"
            )
        return content
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM transport failure ({context})") from exc
    except (TypeError, UnicodeDecodeError, ValueError) as exc:
        raise LLMError(f"LLM request configuration is invalid ({context})") from exc


def chat_json(messages: list[dict], **kwargs: Any) -> Any:
    return extract_json(chat(messages, **kwargs))
