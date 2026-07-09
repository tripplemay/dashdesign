"""Zero-config client bootstrap for the internal tool.

Ordinary users set nothing: the app ships knowing the cloud endpoint and carries
a shared client token, then fetches the shared app-config (image API endpoint /
key / model, and an optional baseline-endpoint override) from the cloud on
startup. Only an admin — via the admin password — edits that config and pushes it
back; every client picks it up on the next fetch.

The cloud endpoint is a plain baked-in constant. The shared client token is a
secret, so it is read from ``ui/_secrets.py`` (git-ignored, present on the build
machine) or the ``DASHDESIGN_CLIENT_TOKEN`` env var — never committed.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import requests
from PySide6.QtCore import QSettings

# Where /app-config and (by default) baselines live. Overridable for dev.
CLOUD_ENDPOINT = (os.environ.get("DASHDESIGN_CLOUD_ENDPOINT") or "https://dash.vpanel.cc").rstrip("/")

try:  # baked at build time; kept out of git
    from ui._secrets import CLIENT_TOKEN as _BAKED_TOKEN
except Exception:  # noqa: BLE001
    _BAKED_TOKEN = ""
CLIENT_TOKEN = os.environ.get("DASHDESIGN_CLIENT_TOKEN") or _BAKED_TOKEN

_CONFIG_CACHE_KEY = "cloud/app_config"
_TIMEOUT = 8


def bootstrap_endpoint() -> str:
    return CLOUD_ENDPOINT


def client_token() -> str:
    return CLIENT_TOKEN


def is_configured() -> bool:
    """True when the app is wired for the cloud (endpoint + shared token)."""
    return bool(CLOUD_ENDPOINT and CLIENT_TOKEN)


def cached_app_config() -> Dict[str, Any]:
    raw = QSettings().value(_CONFIG_CACHE_KEY, "")
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _store_cache(config: Dict[str, Any]) -> None:
    QSettings().setValue(_CONFIG_CACHE_KEY, json.dumps(config, ensure_ascii=False))


def baseline_endpoint() -> str:
    """Where baselines live: the config override if set, else the bootstrap host."""
    override = str(cached_app_config().get("baseline_endpoint", "") or "").strip()
    return (override or CLOUD_ENDPOINT).rstrip("/")


def fetch_app_config() -> Dict[str, Any]:
    """Refresh the shared config from the cloud; fall back to the cache offline."""
    if not is_configured():
        return cached_app_config()
    try:
        resp = requests.get(
            f"{CLOUD_ENDPOINT}/app-config",
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        config = resp.json()
        if isinstance(config, dict):
            _store_cache(config)
            return config
    except (requests.RequestException, ValueError):
        pass
    return cached_app_config()


def bootstrap_async() -> None:
    """Refresh the shared config in the background at startup (never blocks UI)."""
    if not is_configured():
        return
    import threading

    threading.Thread(target=fetch_app_config, daemon=True).start()


def verify_admin(password: str) -> bool:
    resp = requests.post(
        f"{CLOUD_ENDPOINT}/admin/verify",
        headers={"X-Admin-Password": password},
        timeout=_TIMEOUT,
    )
    return resp.status_code == 200


def push_app_config(password: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Admin-only: upload the shared config. Raises on failure."""
    resp = requests.put(
        f"{CLOUD_ENDPOINT}/app-config",
        headers={"X-Admin-Password": password},
        json=config,
        timeout=_TIMEOUT,
    )
    if resp.status_code == 403:
        raise PermissionError("管理密码错误")
    resp.raise_for_status()
    saved = resp.json()
    if isinstance(saved, dict):
        _store_cache(saved)
        return saved
    return config
