"""Thin OpenAI-compatible chat client for baseline extraction.

Reuses the same gateway as image generation (OPENAI_BASE_URL / OPENAI_API_KEY).
Kept separate from ``extract.py`` so the extraction core stays testable with an
injected chat callable; this module is only the network adapter.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests

DEFAULT_MODEL = os.environ.get("DASHDESIGN_BASELINE_MODEL", "qwen3.5-plus")
_TIMEOUT = 180


def make_chat(base_url: str = "", api_key: str = "", model: str = ""):
    """Return a ``chat(messages, response_format=...) -> str`` callable."""
    base = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    model_name = model or DEFAULT_MODEL

    def chat(messages: List[Dict[str, Any]], response_format: Optional[Dict[str, Any]] = None) -> str:
        if not key:
            raise RuntimeError("未配置 API Key（请在 文件 → 设置 中填写）")
        payload: Dict[str, Any] = {"model": model_name, "messages": messages, "temperature": 0.1}
        if response_format is not None:
            payload["response_format"] = response_format
        resp = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=_TIMEOUT,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"模型调用失败 HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    return chat
