"""Graphical progress panel: stage stepper + determinate/busy bar + ETA.

Renders a :class:`ui.progress.ProgressModel` for non-technical users. Falls
back to a busy bar with elapsed time when a workflow emits no progress events.
"""

from __future__ import annotations

from typing import List

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.progress import FAIL, OK, PENDING, RUNNING, SKIP, ProgressModel

_STATUS_ICON = {
    PENDING: "mdi6.circle-outline",
    RUNNING: "mdi6.progress-clock",
    OK: "mdi6.check-circle",
    SKIP: "mdi6.minus-circle-outline",
    FAIL: "mdi6.close-circle",
}

# 运行阶段名里出现这些词时，提示"此步骤耗时较长"，给用户预期。
_SLOW_HINTS = ("API", "生成", "超分", "修复", "放大", "处理")


def _fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} 秒"
    return f"{seconds // 60} 分 {seconds % 60} 秒"


class _StageRow(QWidget):
    def __init__(self, label: str, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(8)
        self.icon = QLabel()
        self.icon.setFixedWidth(20)
        self.text = QLabel(label)
        self.text.setWordWrap(True)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.text, 1)

    def render(self, label: str, status: str, colors: dict) -> None:
        self.text.setText(label)
        color = {
            PENDING: colors["subtitle_fg"],
            RUNNING: colors["accent"],
            OK: colors["success_fg"],
            SKIP: colors["subtitle_fg"],
            FAIL: colors["error_fg"],
        }.get(status, colors["subtitle_fg"])
        self.icon.setPixmap(qta.icon(_STATUS_ICON.get(status, _STATUS_ICON[PENDING]), color=color).pixmap(18, 18))
        weight = "600" if status == RUNNING else "400"
        self.text.setStyleSheet(f"color: {color}; font-weight: {weight};")


class ProgressPanel(QGroupBox):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__("运行进度", parent)
        self.setObjectName("ProgressPanel")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        self.status_label = QLabel("")
        self.status_label.setObjectName("ProgressStatus")
        top.addWidget(self.bar, 1)
        top.addWidget(self.status_label, 0)
        layout.addLayout(top)

        self.stages_container = QWidget()
        self.stages_layout = QVBoxLayout(self.stages_container)
        self.stages_layout.setContentsMargins(0, 0, 0, 0)
        self.stages_layout.setSpacing(0)
        layout.addWidget(self.stages_container)

        self.hint_label = QLabel("")
        self.hint_label.setObjectName("Subtitle")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self._rows: List[_StageRow] = []
        self._row_labels: List[str] = []
        self.hide()

    def reset(self) -> None:
        self.bar.setRange(0, 0)  # busy
        self.status_label.setText("正在启动…")
        self.status_label.setStyleSheet("")  # 清除上次失败留下的红色
        self.hint_label.setText("")
        self._clear_rows()
        self.show()

    def _clear_rows(self) -> None:
        while self.stages_layout.count():
            item = self.stages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) 立即从显示中移除；deleteLater 是异步的，
                # 只 deleteLater 会让旧行在被销毁前继续覆盖到新行上（残影）。
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []
        self._row_labels = []

    def _ensure_rows(self, labels: List[str]) -> None:
        if labels == self._row_labels:
            return
        self._clear_rows()
        for label in labels:
            row = _StageRow(label)
            self.stages_layout.addWidget(row)
            self._rows.append(row)
        self._row_labels = list(labels)

    def render(self, model: ProgressModel, elapsed_seconds: int, running: bool) -> None:
        colors = theme.current_tokens()
        labels = [stage.label for stage in model.stages]
        self._ensure_rows(labels)
        for row, stage in zip(self._rows, model.stages):
            row.render(stage.label, stage.status, colors)

        if not model.has_signal:
            self.bar.setRange(0, 0)
            self.status_label.setText(f"运行中 · {_fmt_duration(elapsed_seconds)}")
            self.hint_label.setText("正在准备工作流…")
            return

        if model.finished:
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.status_label.setText(f"已完成 · 用时 {_fmt_duration(elapsed_seconds)}")
            self.hint_label.setText("")
            return

        if model.is_determinate():
            self.bar.setRange(0, model.step_total)
            self.bar.setValue(model.step_done)
            eta = self._eta(model.step_done, model.step_total, elapsed_seconds)
            status = f"{model.step_done}/{model.step_total}"
            if eta:
                status += f" · 预计剩余 {eta}"
            self.status_label.setText(status)
            self.hint_label.setText(
                f"当前：{model.step_label}" if model.step_label else ""
            )
            return

        # 仅阶段（无内层循环）：进度条按阶段推进。
        total = model.stage_count()
        done = sum(1 for stage in model.stages if stage.status == OK)
        current = model.current_stage_index()
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.status_label.setText(f"步骤 {max(current, done)}/{total}")
        else:
            self.bar.setRange(0, 0)
            self.status_label.setText(f"运行中 · {_fmt_duration(elapsed_seconds)}")
        self.hint_label.setText(self._running_hint(model, elapsed_seconds))

    def _running_hint(self, model: ProgressModel, elapsed_seconds: int) -> str:
        current = model.current_stage_index()
        if current and current <= len(model.stages):
            label = model.stages[current - 1].label
            if any(word in label for word in _SLOW_HINTS):
                return f"“{label}”耗时较长（可能数十秒到数分钟），已用时 {_fmt_duration(elapsed_seconds)}，请耐心等待。"
        return f"已用时 {_fmt_duration(elapsed_seconds)}"

    def _eta(self, done: int, total: int, elapsed_seconds: int) -> str:
        if done <= 0 or elapsed_seconds <= 0 or done >= total:
            return ""
        remaining = elapsed_seconds / done * (total - done)
        return "约 " + _fmt_duration(round(remaining))

    def finalize(self, model: ProgressModel, success: bool, elapsed_seconds: int) -> None:
        colors = theme.current_tokens()
        # 先把阶段状态收尾，再重绘阶段行，让失败所在阶段显示为失败而非"进行中"。
        if success:
            model.mark_all_ok()
        else:
            model.mark_failed()
        self._ensure_rows([stage.label for stage in model.stages])
        for row, stage in zip(self._rows, model.stages):
            row.render(stage.label, stage.status, colors)

        # 无论成败都把忙碌态进度条切成静态，停止无限滚动动画。
        self.bar.setRange(0, 1)
        if success:
            self.bar.setValue(1)
            self.status_label.setText(f"已完成 · 用时 {_fmt_duration(elapsed_seconds)}")
            self.status_label.setStyleSheet("")
            self.hint_label.setText("")
        else:
            self.bar.setValue(0)
            self.status_label.setText(f"运行失败 · 用时 {_fmt_duration(elapsed_seconds)}")
            self.status_label.setStyleSheet(f"color: {colors['error_fg']};")
            self.hint_label.setText("详情见失败提示；可用“文件 → 导出运行日志”保存完整输出以便排查。")
