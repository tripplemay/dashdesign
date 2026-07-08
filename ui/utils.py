"""Small Qt helpers shared across UI modules."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QMessageBox, QScrollArea, QVBoxLayout, QWidget


def open_path(parent: QWidget, path: Path) -> None:
    path = path.expanduser()
    if not path.exists():
        QMessageBox.warning(parent, "路径不存在", str(path))
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def scrollable_page_layout(page: QWidget) -> QVBoxLayout:
    """给页面套一层可滚动内容区，返回内容布局。

    窗口高度不足时页面出现滚动条，而不是把控件压缩到最小高度以下
    （自动换行的提示标签被压缩后会绘制到相邻行上，造成界面重叠）。
    """
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.setSpacing(12)
    scroll.setWidget(content)
    outer.addWidget(scroll)
    return layout
