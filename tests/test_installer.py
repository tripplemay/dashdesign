"""Unit tests for the Qt-free install-channel detection helpers."""

from __future__ import annotations

from pathlib import Path

import os

import pytest

from ui.installer import (
    CHANNEL_FILE,
    is_installed_build,
    launch_windows_installer,
    read_channel,
)


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


class TestLaunchWindowsInstaller:
    """os.startfile is Windows-only; monkeypatch it so the wrapper contract is
    testable everywhere (verifies it elevates via runas and never silently
    no-ops on failure)."""

    def test_elevates_via_runas(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list = []
        monkeypatch.setattr(os, "startfile", lambda p, op=None: calls.append((p, op)), raising=False)
        exe = tmp_path / "setup.exe"
        exe.write_bytes(b"x")
        assert launch_windows_installer(exe) is True
        assert calls == [(str(exe), "runas")]

    def test_returns_false_on_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(path: str, op: str = "") -> None:
            raise OSError("UAC declined")

        monkeypatch.setattr(os, "startfile", boom, raising=False)
        assert launch_windows_installer(tmp_path / "setup.exe") is False
