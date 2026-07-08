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
