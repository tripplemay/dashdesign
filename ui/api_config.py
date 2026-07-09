"""Effective API credentials for image workflows.

For the internal cloud tool these come from the shared app-config the admin
pushes to the cloud (fetched by every client — ordinary users configure nothing).
A per-machine QSettings override and the OPENAI_* env vars remain as fallbacks
(dev / offline / self-hosted-without-cloud), applied in that order.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

from ui import cloud_bootstrap

_BASE_URL_KEY = "api/base_url"
_API_KEY_KEY = "api/key"
_BASELINE_MODEL_KEY = "api/baseline_model"

# 文档合并抽取用的文本模型默认值。不同网关支持的模型不同（有的只支持 OpenAI 系）。
DEFAULT_BASELINE_MODEL = "gpt-4o"


def _cloud() -> dict:
    return cloud_bootstrap.cached_app_config()


def _local(key: str) -> str:
    return str(QSettings().value(key, "") or "").strip()


def load_base_url() -> str:
    return str(_cloud().get("image_api_base_url", "") or "").strip() or _local(_BASE_URL_KEY)


def load_api_key() -> str:
    return str(_cloud().get("image_api_key", "") or "").strip() or _local(_API_KEY_KEY)


def load_baseline_model() -> str:
    return (
        str(_cloud().get("baseline_model", "") or "").strip()
        or _local(_BASELINE_MODEL_KEY)
        or DEFAULT_BASELINE_MODEL
    )


def save(base_url: str, api_key: str, baseline_model: str = "") -> None:
    """Persist a per-machine override (dev / self-hosted). Cloud config wins over this."""
    settings = QSettings()
    settings.setValue(_BASE_URL_KEY, base_url.strip())
    settings.setValue(_API_KEY_KEY, api_key.strip())
    settings.setValue(_BASELINE_MODEL_KEY, baseline_model.strip() or DEFAULT_BASELINE_MODEL)


def has_api_key() -> bool:
    """A key is available from the cloud config, a local override, or the env."""
    return bool(load_api_key() or os.environ.get("OPENAI_API_KEY", "").strip())
