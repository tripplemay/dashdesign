"""Small Qt helpers shared across UI modules."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget


def open_path(parent: QWidget, path: Path) -> None:
    path = path.expanduser()
    if not path.exists():
        QMessageBox.warning(parent, "路径不存在", str(path))
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
