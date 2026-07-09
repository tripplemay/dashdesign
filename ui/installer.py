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
    """Start the downloaded Inno Setup installer detached; return True on success.

    The caller should quit the app immediately afterwards so files unlock. The
    new setup.exe carries CloseApplications/RestartApplications and a
    post-install ``Launch DashDesign`` step to bring the app back up.
    """
    from PySide6.QtCore import QProcess

    started, _pid = QProcess.startDetached(str(Path(setup_exe)), [])
    return bool(started)
