"""Unit tests for the zero-config client bootstrap (cache + offline fallback)."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from ui import api_config, cloud_bootstrap


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    app.setOrganizationName("DashDesignBootstrapTest")
    app.setApplicationName("DashDesignBootstrapTest")
    QSettings().clear()
    # Deterministic baked values regardless of ui/_secrets.py presence.
    monkeypatch.setattr(cloud_bootstrap, "CLOUD_ENDPOINT", "https://cloud.test")
    monkeypatch.setattr(cloud_bootstrap, "CLIENT_TOKEN", "client-tok")
    yield
    QSettings().clear()


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise cloud_bootstrap.requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_is_configured_true_with_baked_values():
    assert cloud_bootstrap.is_configured() is True


def test_cached_config_round_trip():
    cloud_bootstrap._store_cache({"image_api_key": "sk-1", "baseline_model": "m"})
    assert cloud_bootstrap.cached_app_config()["image_api_key"] == "sk-1"


def test_fetch_stores_and_returns(monkeypatch):
    payload = {"baseline_endpoint": "", "image_api_base_url": "https://gw", "image_api_key": "sk-2", "baseline_model": "gpt-4o"}
    monkeypatch.setattr(cloud_bootstrap.requests, "get", lambda *a, **k: _FakeResp(200, payload))
    got = cloud_bootstrap.fetch_app_config()
    assert got["image_api_key"] == "sk-2"
    assert cloud_bootstrap.cached_app_config() == payload  # cached for offline


def test_fetch_falls_back_to_cache_offline(monkeypatch):
    cloud_bootstrap._store_cache({"image_api_key": "sk-cached"})

    def _boom(*a, **k):
        raise cloud_bootstrap.requests.ConnectionError("down")

    monkeypatch.setattr(cloud_bootstrap.requests, "get", _boom)
    assert cloud_bootstrap.fetch_app_config()["image_api_key"] == "sk-cached"


def test_baseline_endpoint_prefers_override():
    cloud_bootstrap._store_cache({"baseline_endpoint": "https://other.host/"})
    assert cloud_bootstrap.baseline_endpoint() == "https://other.host"
    cloud_bootstrap._store_cache({"baseline_endpoint": ""})
    assert cloud_bootstrap.baseline_endpoint() == "https://cloud.test"


def test_api_config_reads_cloud_first(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cloud_bootstrap._store_cache(
        {"image_api_base_url": "https://cloud-gw/v1", "image_api_key": "sk-cloud", "baseline_model": "gpt-5.5"}
    )
    assert api_config.load_base_url() == "https://cloud-gw/v1"
    assert api_config.load_api_key() == "sk-cloud"
    assert api_config.load_baseline_model() == "gpt-5.5"
    assert api_config.has_api_key() is True


def test_verify_and_push(monkeypatch):
    calls = {}

    def _post(url, headers=None, **k):
        calls["verify"] = headers.get("X-Admin-Password")
        return _FakeResp(200, {"ok": True})

    def _put(url, headers=None, json=None, **k):
        calls["put_pw"] = headers.get("X-Admin-Password")
        calls["put_body"] = json
        return _FakeResp(200, json)

    monkeypatch.setattr(cloud_bootstrap.requests, "post", _post)
    monkeypatch.setattr(cloud_bootstrap.requests, "put", _put)
    assert cloud_bootstrap.verify_admin("pw") is True
    assert calls["verify"] == "pw"
    saved = cloud_bootstrap.push_app_config("pw", {"image_api_key": "sk-x"})
    assert saved["image_api_key"] == "sk-x"
    assert cloud_bootstrap.cached_app_config()["image_api_key"] == "sk-x"


def test_push_wrong_password_raises(monkeypatch):
    monkeypatch.setattr(cloud_bootstrap.requests, "put", lambda *a, **k: _FakeResp(403, {"code": "forbidden"}))
    with pytest.raises(PermissionError):
        cloud_bootstrap.push_app_config("bad", {})


def test_change_admin_password(monkeypatch):
    sent = {}

    def _post(url, json=None, **k):
        sent["url"] = url
        sent["body"] = json
        return _FakeResp(200, {"ok": True})

    monkeypatch.setattr(cloud_bootstrap.requests, "post", _post)
    cloud_bootstrap.change_admin_password("old", "newpass1")
    assert sent["url"].endswith("/admin/change-password")
    assert sent["body"] == {"current_password": "old", "new_password": "newpass1"}


def test_change_admin_password_wrong_current_raises(monkeypatch):
    monkeypatch.setattr(cloud_bootstrap.requests, "post", lambda *a, **k: _FakeResp(403, {}))
    with pytest.raises(PermissionError):
        cloud_bootstrap.change_admin_password("bad", "newpass1")
