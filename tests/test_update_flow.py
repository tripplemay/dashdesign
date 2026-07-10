"""Regression test for the auto-update download→install hand-off.

Closing the QProgressDialog programmatically emits its ``canceled`` signal; the
handler set ``_update_cancelled=True`` and the completion path then skipped the
installer, so the installer was NEVER launched (the real reason every launcher
fix "did nothing"). This locks the fix: a normal completion must launch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from update_core import UpdateInfo


@pytest.fixture
def window():
    # QApplication is ensured by the session-scoped conftest fixture.
    from desktop_qt_app import DashDesignQtApp

    win = DashDesignQtApp()
    yield win
    win.close()


def _info() -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        url="https://example/DashDesign-9.9.9-windows-setup.exe",
        sha256="",
        size=1000,
        notes="",
    )


def test_closing_progress_dialog_is_not_treated_as_cancel(window, monkeypatch) -> None:
    # 不真正联网：把下载启动替换成空操作，只保留对话框/信号搭建。
    monkeypatch.setattr("ui.main_window.download_update", lambda *a, **k: None)
    window._start_update_download(_info())
    assert window._update_cancelled is False
    window._close_update_dialog()
    # 关闭对话框不得把"正常完成"误判成用户取消。
    assert window._update_cancelled is False


def test_completed_download_launches_installer(window, monkeypatch) -> None:
    monkeypatch.setattr("ui.main_window.download_update", lambda *a, **k: None)
    launched: list = []
    monkeypatch.setattr("ui.installer.launch_windows_installer", lambda p: launched.append(p) or True)

    window._start_update_download(_info())
    window._on_update_downloaded("/tmp/DashDesign-9.9.9-windows-setup.exe")

    assert launched == [Path("/tmp/DashDesign-9.9.9-windows-setup.exe")]


def test_user_cancel_still_skips_installer(window, monkeypatch) -> None:
    monkeypatch.setattr("ui.main_window.download_update", lambda *a, **k: None)
    launched: list = []
    monkeypatch.setattr("ui.installer.launch_windows_installer", lambda p: launched.append(p) or True)

    window._start_update_download(_info())
    window._cancel_update_download()  # 真·用户取消
    window._on_update_downloaded("/tmp/DashDesign-9.9.9-windows-setup.exe")

    assert launched == []  # 取消后不应启动安装器
