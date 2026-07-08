"""Read-only structured preview of the current project baseline."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app_runtime import baseline_path, evidenced_text, text_list
from ui.utils import open_path
from ui.widgets import FlowLayout

_STATUS_LABELS = {
    "draft": "草稿",
    "published": "已发布",
    "deprecated": "已废弃",
}

_AUDIENCE_LABELS = {
    "to_c": "C 端（家长/学生）",
    "to_c_parent_student": "C 端（家长/学生）",
    "to_b": "B 端（合作机构）",
}


class BaselinePage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        meta = QGroupBox("当前项目基线")
        meta_layout = QGridLayout(meta)
        self.baseline_name = QLabel("-")
        self.baseline_version = QLabel("-")
        self.baseline_status = QLabel("-")
        self.baseline_mode = QLabel("-")
        meta_layout.addWidget(QLabel("项目"), 0, 0)
        meta_layout.addWidget(self.baseline_name, 0, 1)
        meta_layout.addWidget(QLabel("版本"), 0, 2)
        meta_layout.addWidget(self.baseline_version, 0, 3)
        meta_layout.addWidget(QLabel("状态"), 1, 0)
        meta_layout.addWidget(self.baseline_status, 1, 1)
        meta_layout.addWidget(QLabel("受众"), 1, 2)
        meta_layout.addWidget(self.baseline_mode, 1, 3)
        meta_layout.setColumnStretch(1, 1)
        meta_layout.setColumnStretch(3, 1)
        layout.addWidget(meta)

        actions = QHBoxLayout()
        refresh = QPushButton("刷新基线")
        refresh.clicked.connect(self.load_baseline)
        open_file = QPushButton("打开 JSON")
        open_file.clicked.connect(lambda: open_path(self, baseline_path()))
        self.refresh_label = QLabel("")
        self.refresh_label.setObjectName("Subtitle")
        actions.addWidget(refresh)
        actions.addWidget(open_file)
        actions.addSpacing(8)
        actions.addWidget(self.refresh_label)
        actions.addStretch(1)
        layout.addLayout(actions)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(0, 0, 8, 0)
        self._sections_layout.setSpacing(12)
        scroll.setWidget(self._sections_container)
        layout.addWidget(scroll, 1)

        self.load_baseline()

    # ------------------------------------------------------------------
    def load_baseline(self) -> None:
        path = baseline_path()
        self._clear_sections()
        if not path.exists():
            self._set_meta("未找到", "-", "-", "-")
            self._add_text_section("错误", f"未找到项目基线文件：\n{path}")
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._set_meta("读取失败", "-", "-", "-")
            self._add_text_section("错误", f"项目基线读取失败：\n{exc}")
            return

        project = payload.get("project", {}) if isinstance(payload, dict) else {}
        status = str(payload.get("status", "-"))
        mode = str(payload.get("target_audience_mode", "-"))
        self._set_meta(
            str(project.get("name", "-")),
            str(payload.get("version", "-")),
            _STATUS_LABELS.get(status, status),
            _AUDIENCE_LABELS.get(mode, mode),
        )
        self.refresh_label.setText(f"已加载 {datetime.now():%H:%M:%S} · {path.name}")
        self._build_sections(payload)

    def _set_meta(self, name: str, version: str, status: str, mode: str) -> None:
        self.baseline_name.setText(name)
        self.baseline_version.setText(version)
        self.baseline_status.setText(status)
        self.baseline_mode.setText(mode)

    def _clear_sections(self) -> None:
        while self._sections_layout.count():
            item = self._sections_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_sections(self, payload: dict) -> None:
        consumer = payload.get("consumer_baseline", {}) if isinstance(payload, dict) else {}
        visual = payload.get("visual_guidelines", {}) if isinstance(payload, dict) else {}
        prompt_policy = payload.get("prompt_policy", {}) if isinstance(payload, dict) else {}

        positioning = evidenced_text(consumer.get("positioning"))
        if positioning:
            self._add_text_section("C 端定位", positioning)

        for title, values in (
            ("核心信息", text_list(consumer.get("core_messages"))),
            ("家长价值", text_list(consumer.get("parent_value"))),
            ("孩子价值", text_list(consumer.get("student_value"))),
            ("课程模块", [str(item) for item in consumer.get("course_modules", []) if str(item).strip()]),
            ("推荐画面", [str(item) for item in visual.get("recommended_scenes", []) if str(item).strip()]),
            ("构图规则", [str(item) for item in visual.get("composition_rules", []) if str(item).strip()]),
        ):
            if values:
                self._add_list_section(title, values)

        blocked = [str(item) for item in consumer.get("blocked_keywords", []) if str(item).strip()]
        if blocked:
            self._add_chip_section("禁止进入 C 端海报的关键词", blocked, "blocked")
        negative = [str(item) for item in prompt_policy.get("negative_constraints", []) if str(item).strip()]
        if negative:
            self._add_chip_section("文生图负面约束", negative, "negative")

        if str(payload.get("source_context", "")) == "to_b_partnership_docs":
            self._add_text_section(
                "注意",
                "当前源资料主要是 to B 合作介绍；客户端只展示并使用转换后的 to C 家长/学生基线。",
            )
        self._sections_layout.addStretch(1)

    def _make_section(self, title: str) -> "tuple[QGroupBox, QVBoxLayout]":
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(6)
        self._sections_layout.addWidget(box)
        return box, box_layout

    def _add_text_section(self, title: str, text: str) -> None:
        _, box_layout = self._make_section(title)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box_layout.addWidget(label)

    def _add_list_section(self, title: str, values: "list[str]") -> None:
        _, box_layout = self._make_section(title)
        for value in values:
            label = QLabel(f"•  {value}")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box_layout.addWidget(label)

    def _add_chip_section(self, title: str, values: "list[str]", chip_kind: str) -> None:
        box = QGroupBox(title)
        flow = FlowLayout(box, margin=8, spacing=6)
        for value in values:
            chip = QLabel(value)
            chip.setProperty("chip", chip_kind)
            flow.addWidget(chip)
        self._sections_layout.addWidget(box)

    def input_preview_path(self) -> "Path | None":
        return None