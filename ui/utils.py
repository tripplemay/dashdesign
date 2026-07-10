"""Small Qt helpers shared across UI modules."""

from __future__ import annotations

import re
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


# (正则模式, 面向运营用户的解释) —— 按先命中先用的顺序排列。
_ERROR_HINTS = (
    (
        r"(?i)connection|timed? ?out|timeout|ssl|getaddrinfo|name resolution|network|refused",
        "网络连接失败，请检查网络后重试。",
    ),
    (
        r"(?i)401|403|unauthorized|invalid[_ ]api[_ ]key|incorrect api key|authentication",
        "API 密钥无效或权限不足，请检查 API 配置。",
    ),
    (
        r"(?i)429|rate.?limit|insufficient_quota|quota",
        "API 配额不足或请求过于频繁，请稍后重试。",
    ),
    (
        r"(?i)filenotfound|no such file|不存在",
        "找不到输入文件或目录，请确认路径存在。",
    ),
    (
        r"(?i)permissionerror|permission denied|拒绝访问|access is denied",
        "没有读写权限，请更换输出目录后重试。",
    ),
    (r"(?i)no space left|磁盘.*满", "磁盘空间不足，请清理后重试。"),
)


def friendly_error_hint(stderr_tail: str) -> str:
    """把常见的原始报错（多半是 Python traceback 尾行）翻译成用户能行动的一句话。

    未识别的错误返回空串，调用方保留原始摘要；完整日志始终可经"导出运行日志"拿到。
    """
    if not stderr_tail:
        return ""
    for pattern, hint in _ERROR_HINTS:
        if re.search(pattern, stderr_tail):
            return hint
    return ""


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
