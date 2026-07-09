#!/usr/bin/env python3
"""Self-contained Chinese->English visual-prompt translation for image workflows.

Kept dependency-free (only ``requests``) on purpose: the packaged
``DashDesignWorker`` executable runs these scripts, and its PyInstaller analysis
does NOT bundle the ``baseline`` package — so this must not import ``baseline``.
It reuses the same ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` env the worker is
already handed (see ``ui.commands.api_env``), plus a text model id passed by the
caller via ``--text-model``.

Translation is best-effort: if the text has no Chinese, no model/credentials are
available, or the call fails, the original text is returned so image generation
is never blocked by translation.
"""

from __future__ import annotations

import json
import os

import requests

_TIMEOUT_SECONDS = 60
_SYSTEM_PROMPT = (
    "You are a professional prompt translator for a text-to-image model. "
    "Translate the user's Chinese visual/scene description into concise, vivid, "
    "concrete English suitable as an image-generation prompt. Keep only visual "
    "content: scene, subject, mood, lighting, composition, and style. Do not add "
    "or invent any rendered text, captions, watermarks, logos, prices, or phone "
    "numbers, and do not explain. Output only the English prompt."
)


def contains_cjk(text: str) -> bool:
    """True if ``text`` contains at least one CJK ideograph."""
    return any("一" <= char <= "鿿" for char in text)


def translate_visual_prompt(
    text: str,
    model: str,
    *,
    base_url: str = "",
    api_key: str = "",
    timeout: int = _TIMEOUT_SECONDS,
) -> str:
    """Translate a Chinese visual prompt to English; fall back to the original.

    Skips the network call when ``text`` is blank, has no Chinese, ``model`` is
    unset, or credentials are missing. Never raises: any network/model/parse
    error returns ``text`` unchanged.
    """
    stripped = text.strip()
    if not stripped or not model or not contains_cjk(stripped):
        return text

    base = (base_url or os.environ.get("OPENAI_BASE_URL", "")).rstrip("/")
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not base or not key:
        return text

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": stripped},
        ],
        "temperature": 0.2,
    }
    try:
        response = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        if response.status_code >= 400:
            return text
        content = response.json()["choices"][0]["message"]["content"]
        # 网关若返回非字符串 content（如 content-parts 列表），也归一为空并降级为
        # 原文——翻译绝不能让出图崩溃。
        translated = content.strip() if isinstance(content, str) else ""
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
        return text

    return translated or text
