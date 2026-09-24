"""Image editing page: edit an existing poster image with AI (single mode)."""

from __future__ import annotations

from pathlib import Path
import json
import uuid

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.output_paths import default_output, restore_output
from ui import api_config, workspace
from ui.commands import GptForm
from ui.print_size import suggest_print_size_cm
from ui.utils import scrollable_page_layout
from ui.widgets import PathField


class GptPage(QWidget):
    resumeRequested = Signal()

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self._pending_package = None
        self._pending_signature = None
        self._submitted_signature = None
        self._pending_revision = None
        self._resume_package = ""
        self._answer_file = ""
        self._running = False
        layout = scrollable_page_layout(self)

        paths = QGroupBox("图片与输出")
        path_layout = QVBoxLayout(paths)
        self.gpt_source = PathField("原图片", "", "file", placeholder="拖入或选择要修改的图片")
        self.gpt_output = PathField(
            "输出目录",
            default_output("workflow_samples", "desktop_gpt_image_rebuild_qt"),
            "dir",
        )
        path_layout.addWidget(self.gpt_source)
        path_layout.addWidget(self.gpt_output)
        self.workspace_note = QLabel("已启用工作区：成品图自动保存到「工作区 / 图片修改」。")
        self.workspace_note.setObjectName("Subtitle")
        self.workspace_note.setWordWrap(True)
        path_layout.addWidget(self.workspace_note)
        layout.addWidget(paths)

        settings_group = QGroupBox("修改设置")
        settings_layout = QGridLayout(settings_group)
        settings_layout.addWidget(QLabel("修改要求"), 0, 0)
        self.gpt_description = QPlainTextEdit()
        self.gpt_description.setObjectName("TextPrompt")
        self.gpt_description.setPlaceholderText(
            "直接说明要怎么改，AI 会结合原图整理修改指令；不明确时会先询问，不直接出图。"
        )
        self.gpt_description.setMaximumHeight(96)
        settings_layout.addWidget(self.gpt_description, 0, 1)

        size_tip = "成品实际尺寸，用于计算打印像素。必须填写，否则无法确定物理尺寸。"
        size_row = QHBoxLayout()
        self.gpt_width_cm = QDoubleSpinBox()
        self.gpt_width_cm.setRange(1, 1000)
        self.gpt_width_cm.setDecimals(1)
        self.gpt_width_cm.setValue(120)
        self.gpt_width_cm.setToolTip(size_tip)
        self.gpt_height_cm = QDoubleSpinBox()
        self.gpt_height_cm.setRange(1, 1000)
        self.gpt_height_cm.setDecimals(1)
        self.gpt_height_cm.setValue(80)
        self.gpt_height_cm.setToolTip(size_tip)
        size_row.addWidget(QLabel("宽 cm"))
        size_row.addWidget(self.gpt_width_cm)
        size_row.addSpacing(12)
        size_row.addWidget(QLabel("高 cm"))
        size_row.addWidget(self.gpt_height_cm)
        size_row.addStretch(1)
        settings_layout.addWidget(QLabel("成品尺寸"), 1, 0)
        settings_layout.addLayout(size_row, 1, 1)
        # 选图后按源图(文件名尺寸/print_spec/目录名，否则像素比例)自动预填成品尺寸，
        # 让显示值与实际出图尺寸一致，避免默认值静默覆盖带尺寸命名的源图。
        self.gpt_source.edit.textChanged.connect(self._prefill_size_from_source)

        dpi_row = QHBoxLayout()
        self.gpt_dpi = QSpinBox()
        self.gpt_dpi.setRange(30, 600)
        self.gpt_dpi.setValue(200)
        self.gpt_dpi.setToolTip("印刷输出分辨率：写真/展架常用 200，大幅喷绘可用 150。")
        dpi_row.addWidget(self.gpt_dpi)
        dpi_row.addStretch(1)
        settings_layout.addWidget(QLabel("DPI"), 2, 0)
        settings_layout.addLayout(dpi_row, 2, 1)
        settings_layout.setColumnStretch(1, 1)
        layout.addWidget(settings_group)

        self.interpretation_group = QGroupBox("AI 理解结果（展开查看）")
        self.interpretation_group.setCheckable(True)
        self.interpretation_group.setChecked(False)
        interpretation_layout = QVBoxLayout(self.interpretation_group)
        self.interpretation_text = QPlainTextEdit()
        self.interpretation_text.setReadOnly(True)
        self.interpretation_text.setPlaceholderText("转写后显示中文修改摘要和最终提示词，不会覆盖你的原始要求。")
        self.interpretation_text.setMaximumHeight(220)
        interpretation_layout.addWidget(self.interpretation_text)
        self.interpretation_text.setVisible(False)
        self.interpretation_group.toggled.connect(self.interpretation_text.setVisible)
        layout.addWidget(self.interpretation_group)

        self.clarification_group = QGroupBox("补充修改要求")
        clarification_layout = QVBoxLayout(self.clarification_group)
        self.clarification_question = QLabel()
        self.clarification_question.setTextFormat(Qt.TextFormat.PlainText)
        self.clarification_question.setWordWrap(True)
        self.clarification_answer = QPlainTextEdit()
        self.clarification_answer.setPlaceholderText("回答上面的问题，原始要求会一并保留。")
        self.clarification_answer.setMaximumHeight(85)
        self.resume_button = QPushButton("补充并继续")
        self.resume_button.clicked.connect(lambda: self.resumeRequested.emit())
        clarification_layout.addWidget(self.clarification_question)
        clarification_layout.addWidget(self.clarification_answer)
        clarification_layout.addWidget(self.resume_button)
        self.clarification_group.hide()
        layout.addWidget(self.clarification_group)
        self.gpt_source.edit.textChanged.connect(self._invalidate_pending)
        self.gpt_description.textChanged.connect(self._invalidate_pending)
        for spin in (self.gpt_width_cm, self.gpt_height_cm, self.gpt_dpi):
            spin.valueChanged.connect(self._invalidate_pending)
        layout.addStretch(1)
        self.refresh_workspace()

    def refresh_workspace(self) -> None:
        workspace.apply_output_field(self.gpt_output, self.workspace_note)

    def _prefill_size_from_source(self, text: str) -> None:
        source = text.strip()
        if not source:
            return
        size = suggest_print_size_cm(Path(source).expanduser())
        if size is None:
            return
        width_cm, height_cm = size
        self.gpt_width_cm.setValue(width_cm)
        self.gpt_height_cm.setValue(height_cm)

    def confirm_run(self, window) -> bool:  # type: ignore[no-untyped-def]
        if not api_config.has_api_key():
            window.banner.show_message(
                "error",
                api_config.missing_key_message(),
                action_label="打开设置",
                action_callback=window.open_settings,
            )
            return False
        return True

    def form(self) -> GptForm:
        return GptForm(
            optimize_prompt=True,
            agent_model=api_config.load_edit_agent_model(),
            resume_package=self._resume_package,
            answer_file=self._answer_file,
            image_model=api_config.load_image_model(),
            source=self.gpt_source.text(),
            output_dir=workspace.effective_output_dir(
                default_output("workflow_samples", "desktop_gpt_image_rebuild_qt"),
                self.gpt_output.text(),
            ),
            width_cm=str(self.gpt_width_cm.value()),
            height_cm=str(self.gpt_height_cm.value()),
            dpi=str(self.gpt_dpi.value()),
            description=self.gpt_description.toPlainText(),
            base_url=api_config.load_base_url(),
            api_key=api_config.load_api_key(),
        )

    def _signature(self) -> tuple:
        source = Path(self.gpt_source.text()).expanduser()
        try:
            stat = source.stat()
            stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            stamp = None
        return (str(source.resolve()), stamp, self.gpt_description.toPlainText(),
                self.gpt_width_cm.value(), self.gpt_height_cm.value(), self.gpt_dpi.value(),
                api_config.load_edit_agent_model(), api_config.load_image_model())

    def _invalidate_pending(self, *args) -> None:
        self._pending_package = self._pending_signature = None
        self._pending_revision = None
        self._resume_package = self._answer_file = ""
        self.clarification_group.hide()
        self.interpretation_text.clear()

    def prepare_run(self) -> None:
        signature = self._signature()
        self._resume_package = self._answer_file = ""
        if self._pending_package is not None and signature == self._pending_signature:
            state = json.loads((self._pending_package / "edit_state.json").read_text(encoding="utf-8"))
            if state["revision"] != self._pending_revision:
                raise ValueError("当前问题已过期，请重新开始任务，不能提交旧问题的回答")
            if state["phase"] == "needs_input":
                answer = self.clarification_answer.toPlainText().strip()
                if not answer:
                    raise ValueError("请先回答 Agent 的问题，再继续修改")
                directory = self._pending_package / "answers"
                directory.mkdir(exist_ok=True)
                path = directory / (uuid.uuid4().hex + ".json")
                path.write_text(json.dumps({"revision": state["revision"], "answer": answer}, ensure_ascii=False), encoding="utf-8")
                self._answer_file = str(path)
            elif state["phase"] != "agent_failed":
                raise ValueError("任务状态已变化，不能重复续跑，请重新填写修改要求")
            self._resume_package = str(self._pending_package)
        else:
            self._invalidate_pending()
        self._submitted_signature = signature

    def set_running(self, running: bool) -> None:
        self._running = running
        self.resume_button.setEnabled(not running)

    def show_interpretation(self, summary: str, prompt: str) -> None:
        if self._signature() == self._submitted_signature:
            self.interpretation_text.setPlainText(summary + ("\n\n最终提示词：\n" + prompt if prompt else ""))

    def finish_edit(self, package: Path, outcome: str) -> None:
        if self._signature() != self._submitted_signature:
            return
        self._pending_package = self._pending_signature = None
        self._resume_package = self._answer_file = ""
        self.clarification_group.hide()
        if outcome == "cancelled":
            return
        try:
            state = json.loads((package / "edit_state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if state.get("phase") not in {"needs_input", "agent_failed"}:
            return
        self._pending_package, self._pending_signature = package, self._submitted_signature
        self._pending_revision = state["revision"]
        needs_input = state["phase"] == "needs_input"
        self.clarification_question.setText(state["question"] if needs_input else "Agent 转写失败，尚未调用图片模型。可重试转写或修改原始要求。")
        self.clarification_answer.clear()
        self.clarification_answer.setVisible(needs_input)
        self.resume_button.setText("补充并继续" if needs_input else "重试转写")
        self.resume_button.setEnabled(not self._running)
        self.clarification_group.show()

    def input_preview_path(self) -> "Path | None":
        if not self.gpt_source.text():
            return None
        path = Path(self.gpt_source.text()).expanduser()
        return path if path.exists() else None

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/gpt/output_dir", self.gpt_output.text())
        settings.setValue("pages/gpt/width_cm", self.gpt_width_cm.value())
        settings.setValue("pages/gpt/height_cm", self.gpt_height_cm.value())
        settings.setValue("pages/gpt/dpi", self.gpt_dpi.value())

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.gpt_output.setText(
            restore_output(
                str(settings.value("pages/gpt/output_dir", "")),
                "workflow_samples",
                "desktop_gpt_image_rebuild_qt",
            )
        )
        self.gpt_width_cm.setValue(
            settings.value("pages/gpt/width_cm", self.gpt_width_cm.value(), type=float)
        )
        self.gpt_height_cm.setValue(
            settings.value("pages/gpt/height_cm", self.gpt_height_cm.value(), type=float)
        )
        self.gpt_dpi.setValue(settings.value("pages/gpt/dpi", self.gpt_dpi.value(), type=int))
