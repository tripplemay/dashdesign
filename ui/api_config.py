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


def load_base_url() -> str:
    return str(QSettings().value(_BASE_URL_KEY, "") or "").strip()


def load_api_key() -> str:
    return str(QSettings().value(_API_KEY_KEY, "") or "").strip()


def save(base_url: str, api_key: str) -> None:
    settings = QSettings()
    settings.setValue(_BASE_URL_KEY, base_url.strip())
    settings.setValue(_API_KEY_KEY, api_key.strip())


def has_api_key() -> bool:
    """A key is available if it is persisted or present in the inherited env."""
    return bool(load_api_key() or os.environ.get("OPENAI_API_KEY", "").strip())
