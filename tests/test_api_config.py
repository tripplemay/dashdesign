"""Unit tests for the persisted API credential store."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from ui import api_config


@pytest.fixture(autouse=True)
def _isolated_settings():
    app = QCoreApplication.instance() or QCoreApplication([])
    app.setOrganizationName("DashDesignTest")
    app.setApplicationName("DashDesignTest")
    QSettings().clear()
    yield
    QSettings().clear()


def test_defaults_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert api_config.load_base_url() == ""
    assert api_config.load_api_key() == ""
    assert api_config.has_api_key() is False


def test_save_and_load_round_trip() -> None:
    api_config.save("  https://gw.example/v1  ", "  sk-abc  ")
    assert api_config.load_base_url() == "https://gw.example/v1"
    assert api_config.load_api_key() == "sk-abc"
    assert api_config.has_api_key() is True


def test_has_api_key_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    QSettings().clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert api_config.load_api_key() == ""  # 未持久化
    assert api_config.has_api_key() is True  # 但环境变量可用


def test_empty_env_and_store_means_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    QSettings().clear()
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert api_config.has_api_key() is False


def test_image_model_is_independent_and_cloud_wins(monkeypatch):
    monkeypatch.setattr(api_config, "_cloud", lambda: {})
    assert api_config.load_image_model() == "gpt-image-2"
    api_config.save("https://gw/v1", "test-key", "text-custom", " image-custom ")
    assert api_config.load_image_model() == "image-custom"
    assert api_config.load_baseline_model() == "text-custom"
    monkeypatch.setattr(api_config, "_cloud", lambda: {"image_model": "cloud-image"})
    assert api_config.load_image_model() == "cloud-image"
    monkeypatch.setattr(api_config, "_cloud", lambda: {"image_model": "  "})
    assert api_config.load_image_model() == "image-custom"


def test_edit_agent_model_priority_and_legacy_save(monkeypatch):
    monkeypatch.setattr(api_config, "_cloud", lambda: {})
    api_config.save("https://gw/v1", "key", "text-custom", "image-custom")
    assert api_config.load_edit_agent_model() == "text-custom"
    api_config.save("https://gw/v1", "key", "text-custom", "image-custom", " vision-local ")
    api_config.save("https://gw/v1", "key", "text-custom", "image-custom")
    assert api_config.load_edit_agent_model() == "vision-local"
    monkeypatch.setattr(api_config, "_cloud", lambda: {"edit_agent_model": " vision-cloud "})
    assert api_config.load_edit_agent_model() == "vision-cloud"
    monkeypatch.setattr(api_config, "_cloud", lambda: {"edit_agent_model": "", "baseline_model": "text-cloud"})
    assert api_config.load_edit_agent_model() == "vision-local"
    api_config.save("https://gw/v1", "key", "text-custom", "image-custom", "")
    assert api_config.load_edit_agent_model() == "text-cloud"
