"""Read-only preview of the current project baseline."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app_runtime import baseline_path, evidenced_text, text_list
from ui.utils import open_path


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
        meta_layout.addWidget(QLabel("版本"), 1, 0)
        meta_layout.addWidget(self.baseline_version, 1, 1)
        meta_layout.addWidget(QLabel("状态"), 2, 0)
        meta_layout.addWidget(self.baseline_status, 2, 1)
        meta_layout.addWidget(QLabel("受众"), 3, 0)
        meta_layout.addWidget(self.baseline_mode, 3, 1)
        meta_layout.setColumnStretch(1, 1)
        layout.addWidget(meta)

        actions = QHBoxLayout()
        refresh = QPushButton("刷新基线")
        refresh.clicked.connect(self.load_baseline)
        open_file = QPushButton("打开 JSON")
        open_file.clicked.connect(lambda: open_path(self, baseline_path()))
        actions.addWidget(refresh)
        actions.addWidget(open_file)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.baseline_summary = QPlainTextEdit()
        self.baseline_summary.setObjectName("BaselineSummary")
        self.baseline_summary.setReadOnly(True)
        self.baseline_summary.setMinimumHeight(360)
        layout.addWidget(self.baseline_summary, 1)

        self.load_baseline()

    def load_baseline(self) -> None:
        path = baseline_path()
        if not path.exists():
            self.baseline_name.setText("未找到")
            self.baseline_version.setText("-")
            self.baseline_status.setText("-")
            self.baseline_mode.setText("-")
            self.baseline_summary.setPlainText(f"未找到项目基线文件：\n{path}")
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.baseline_name.setText("读取失败")
            self.baseline_version.setText("-")
            self.baseline_status.setText("-")
            self.baseline_mode.setText("-")
            self.baseline_summary.setPlainText(f"项目基线读取失败：\n{exc}")
            return

        project = payload.get("project", {}) if isinstance(payload, dict) else {}
        self.baseline_name.setText(str(project.get("name", "-")))
        self.baseline_version.setText(str(payload.get("version", "-")))
        self.baseline_status.setText(str(payload.get("status", "-")))
        self.baseline_mode.setText(str(payload.get("target_audience_mode", "-")))
        self.baseline_summary.setPlainText(format_baseline_summary(payload, path))

    def input_preview_path(self) -> "Path | None":
        return None


def format_baseline_summary(payload: dict, path: Path) -> str:
    project = payload.get("project", {}) if isinstance(payload, dict) else {}
    consumer = payload.get("consumer_baseline", {}) if isinstance(payload, dict) else {}
    visual = payload.get("visual_guidelines", {}) if isinstance(payload, dict) else {}
    prompt_policy = payload.get("prompt_policy", {}) if isinstance(payload, dict) else {}

    sections: list[str] = [
        f"文件：{path}",
        "",
        "C 端定位",
        evidenced_text(consumer.get("positioning")),
        "",
        "核心信息",
        *[f"- {item}" for item in text_list(consumer.get("core_messages"))],
        "",
        "家长价值",
        *[f"- {item}" for item in text_list(consumer.get("parent_value"))],
        "",
        "孩子价值",
        *[f"- {item}" for item in text_list(consumer.get("student_value"))],
        "",
        "课程模块",
        *[f"- {item}" for item in consumer.get("course_modules", [])],
        "",
        "推荐画面",
        *[f"- {item}" for item in visual.get("recommended_scenes", [])],
        "",
        "构图规则",
        *[f"- {item}" for item in visual.get("composition_rules", [])],
        "",
        "禁止进入 C 端海报的关键词",
        *[f"- {item}" for item in consumer.get("blocked_keywords", [])],
        "",
        "文生图负面约束",
        *[f"- {item}" for item in prompt_policy.get("negative_constraints", [])],
    ]
    source_context = str(payload.get("source_context", ""))
    if source_context == "to_b_partnership_docs":
        sections.extend(
            [
                "",
                "注意",
                "当前源资料主要是 to B 合作介绍；客户端只展示并使用转换后的 to C 家长/学生基线。",
            ]
        )
    return "\n".join(sections).strip() + "\n"
