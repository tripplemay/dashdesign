"""Persisted, app-wide API credentials (base URL + key).

Single source of truth backed by QSettings (per-user OS store — not the repo,
survives app updates). Shared by every workflow that calls the image API, so
the operator configures it once. The key is stored in plain text in the user
settings store; it is never written into a git-tracked file.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

_BASE_URL_KEY = "api/base_url"
_API_KEY_KEY = "api/key"
_BASELINE_MODEL_KEY = "api/baseline_model"

# 文档合并抽取用的文本模型。默认取一个通用 OpenAI 文本模型；不同网关支持的
# 模型不同（有的只支持 OpenAI 系），用户可在“设置”里改成自己网关支持的名字。
DEFAULT_BASELINE_MODEL = "gpt-4o"


def load_base_url() -> str:
    return str(QSettings().value(_BASE_URL_KEY, "") or "").strip()


def load_api_key() -> str:
    return str(QSettings().value(_API_KEY_KEY, "") or "").strip()


def load_baseline_model() -> str:
    return str(QSettings().value(_BASELINE_MODEL_KEY, "") or "").strip() or DEFAULT_BASELINE_MODEL


def save(base_url: str, api_key: str, baseline_model: str = "") -> None:
    settings = QSettings()
    settings.setValue(_BASE_URL_KEY, base_url.strip())
    settings.setValue(_API_KEY_KEY, api_key.strip())
    settings.setValue(_BASELINE_MODEL_KEY, baseline_model.strip() or DEFAULT_BASELINE_MODEL)


def has_api_key() -> bool:
    """A key is available if it is persisted or present in the inherited env."""
    return bool(load_api_key() or os.environ.get("OPENAI_API_KEY", "").strip())
