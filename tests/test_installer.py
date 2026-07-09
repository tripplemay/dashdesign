"""Unit tests for the Qt-free install-channel detection helpers."""

from __future__ import annotations

from pathlib import Path

from ui.installer import CHANNEL_FILE, is_installed_build, read_channel


class TestReadChannel:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert read_channel(tmp_path) == ""

    def test_reads_and_normalizes(self, tmp_path: Path) -> None:
        (tmp_path / CHANNEL_FILE).write_text("  INSTALLED\n", encoding="utf-8")
        assert read_channel(tmp_path) == "installed"


class TestIsInstalledBuild:
    def test_installed_sentinel_true(self, tmp_path: Path) -> None:
        (tmp_path / CHANNEL_FILE).write_text("installed", encoding="utf-8")
        assert is_installed_build(tmp_path) is True

    def test_portable_without_sentinel_false(self, tmp_path: Path) -> None:
        assert is_installed_build(tmp_path) is False

    def test_other_channel_value_false(self, tmp_path: Path) -> None:
        (tmp_path / CHANNEL_FILE).write_text("portable", encoding="utf-8")
        assert is_installed_build(tmp_path) is False
