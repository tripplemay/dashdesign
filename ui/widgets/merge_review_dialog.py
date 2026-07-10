"""Field-level review of an LLM merge report before it becomes a draft.

Every proposed change is a row the reviewer can accept or reject. Governance
issues (blocked keyword / forbidden claim / low confidence) are pre-unchecked
and color-highlighted; nothing is auto-published. Approving builds a new draft.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from baseline import merge
from ui import theme

_TARGET_LABEL = {
    "consumer_baseline.core_messages": "核心信息（C 端）",
    "consumer_baseline.parent_value": "家长价值（C 端）",
    "consumer_baseline.student_value": "孩子价值（C 端）",
    "source_facts.consumer_safe_facts": "可安全事实",
    "source_facts.business_terms": "B 端溯源",
}


class MergeReviewDialog(QDialog):
    def __init__(self, report: "merge.MergeReport", parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.report = report
        self.setWindowTitle("文档内容审校")
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.resize(min(920, avail.width() - 80), min(560, avail.height() - 80))
        else:
            self.resize(920, 560)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        doc = report.new_document.get("file") or report.new_document.get("document_id") or "上传文档"
        summary = QLabel(
            f"来自《{doc}》的 {len(report.changes)} 条候选。勾选表示采纳进入新草稿；"
            "命中禁用词/疑似承诺/低置信的项已默认取消勾选并高亮，请人工确认。"
            "采纳后仅生成草稿，不会自动发布。"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        # 冲突/需审项置顶
        order = {merge.BLOCKED: 0, merge.FORBIDDEN: 1, merge.LOW_CONF: 2, merge.B_SIDE: 3, merge.OK: 4}
        self._rows = sorted(report.changes, key=lambda c: order.get(c.governance, 9))

        self.table = QTableWidget(len(self._rows), 5)
        self.table.setHorizontalHeaderLabels(["采纳", "目标字段", "新内容", "置信", "分类 / 说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        tokens = theme.current_tokens()
        row_bg = {
            merge.BLOCKED: tokens.get("error_bg"),
            merge.FORBIDDEN: tokens.get("warning_bg"),
            merge.LOW_CONF: tokens.get("warning_bg"),
        }
        for r, change in enumerate(self._rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if change.accepted else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, check)
            self.table.setItem(r, 1, QTableWidgetItem(_TARGET_LABEL.get(change.target, change.target)))
            text_item = QTableWidgetItem(change.text)
            text_item.setToolTip(change.text)
            self.table.setItem(r, 2, text_item)
            self.table.setItem(r, 3, QTableWidgetItem(f"{change.confidence:.2f}"))
            note = change.governance_label + (f" · {change.note}" if change.note else "")
            self.table.setItem(r, 4, QTableWidgetItem(note))
            bg = row_bg.get(change.governance)
            if bg:
                for c in range(5):
                    self.table.item(r, c).setBackground(_qcolor(bg))
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        self.table.resizeColumnToContents(3)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("生成草稿")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        for r, change in enumerate(self._rows):
            change.accepted = self.table.item(r, 0).checkState() == Qt.CheckState.Checked
        self.accept()


_RGBA_RE = re.compile(
    r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)"
)


def _qcolor(value: str):
    """Token 值转 QColor。暗色 token 用 CSS ``rgba(r, g, b, a)`` 字符串——QColor
    不解析该格式（会得到无效色，暗色模式下高亮行退化成黑/透明），需手动拆。"""
    from PySide6.QtGui import QColor

    match = _RGBA_RE.fullmatch(str(value).strip())
    if match:
        r, g, b = (int(float(match.group(i))) for i in (1, 2, 3))
        alpha = float(match.group(4)) if match.group(4) is not None else 1.0
        return QColor(r, g, b, max(0, min(255, round(alpha * 255))))
    return QColor(value)
