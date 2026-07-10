"""Windows install-channel detection and installer hand-off.

Auto-update only drives the *installed* build: the Inno Setup installer can
close the running app, overwrite the copy in Program Files and relaunch it. The
portable zip is the bare PyInstaller folder with no sentinel, so it must fall
back to a manual download instead of launching the installer (which would drop
a second copy into Program Files and leave the portable folder stale).

The detection helpers are Qt-free and unit-tested; the PySide6 import is
deferred into ``launch_windows_installer`` so this module loads without Qt.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# The Inno Setup installer writes this file into {app} (see
# packaging/windows/DashDesign.iss). The portable zip ships without it.
CHANNEL_FILE = "channel.txt"
INSTALLED_CHANNEL = "installed"


def app_dir() -> Path:
    """Directory holding the running executable — Inno's ``{app}`` in a packaged
    build, or the interpreter dir in a dev run."""
    return Path(sys.executable).resolve().parent


def read_channel(directory: Path) -> str:
    """Return the normalized channel sentinel in ``directory`` (``""`` if none)."""
    try:
        return (directory / CHANNEL_FILE).read_text(encoding="utf-8").strip().lower()
    except OSError:
        return ""


def is_installed_build(directory: Optional[Path] = None) -> bool:
    """True only when running from an Inno-installed copy (channel sentinel present).

    False for the portable zip and for dev runs, so those never launch the
    installer.
    """
    if directory is None:
        directory = app_dir()
    return read_channel(directory) == INSTALLED_CHANNEL


def launch_windows_installer(setup_exe: Path) -> bool:
    """Start the downloaded Inno Setup installer elevated; return True once it is
    actually running.

    The installer targets Program Files, so its manifest requests
    ``requireAdministrator``:

    - ``QProcess``/``CreateProcess`` cannot elevate (ERROR_ELEVATION_REQUIRED) —
      the bug v0.3.0/v0.3.1 shipped.
    - ``os.startfile`` (plain ``ShellExecuteW``) elevates, but returns as soon as
      the request is *handed off*, asynchronously. The caller then quits ~300ms
      later, and Windows aborts the still-pending elevation — the installer
      never appears, with no error. That was the v0.3.2–v0.4.0 bug: "downloads
      but never launches".

    The fix is ``ShellExecuteExW`` with ``SEE_MASK_NOASYNC``, which Microsoft
    documents as required when the calling thread exits soon after: it blocks
    until the elevated process is created (i.e. past the UAC prompt), so quitting
    afterwards is safe. Returns False if the launch fails or the user declines
    UAC, so the caller can show the manual-run fallback.
    """
    import ctypes
    from ctypes import wintypes

    class _ShellExecuteInfoW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS = 0x00000040
    SEE_MASK_NOASYNC = 0x00000100  # 关键：调用后即退出进程时必须设，否则提权会被中止
    SW_SHOWNORMAL = 1

    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfoW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL

    info = _ShellExecuteInfoW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"  # 显式请求提权，弹 UAC 并以管理员启动
    info.lpFile = str(Path(setup_exe))
    info.nShow = SW_SHOWNORMAL
    return bool(shell32.ShellExecuteExW(ctypes.byref(info)))
