"""Multi-project baseline manager: project/version selection, lifecycle, preview.

The structured preview stays read-only; version lifecycle (new draft / validate
/ publish / set-active) runs through the Qt-free repository so governance and
append-only versioning are enforced in one place.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app_runtime import evidenced_text, text_list
from baseline import governance, merge
from baseline.errors import BaselineError
from baseline.schema import validation_errors
from baseline import generate as generate_mod
from baseline.store import today_str
from ui import api_config, baseline_service
from ui.merge_job import GenerateSignals, MergeSignals, run_generate_job, run_merge_job
from ui.utils import open_path
from ui.widgets import FlowLayout, MergeReviewDialog, NewProjectDialog, SettingsDialog

_STATUS_LABELS = {"draft": "草稿", "published": "已发布", "archived": "已归档"}
_AUDIENCE_LABELS = {
    "to_c_parent_student": "C 端（家长/学生）",
    "to_b_partnership": "B 端（合作机构）",
    "internal": "内部",
}


class BaselinePage(QWidget):
    projectChanged = Signal()

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # -- 项目 / 版本选择 -------------------------------------------
        selector = QGroupBox("项目与版本")
        sel_grid = QGridLayout(selector)
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self.version_combo = QComboBox()
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        self.new_project_button = QPushButton("新建项目…")
        self.new_project_button.setToolTip("从内置模板/复制现有项目/导入 JSON 新建一个项目基线")
        self.new_project_button.clicked.connect(self._new_project)
        sel_grid.addWidget(QLabel("项目"), 0, 0)
        sel_grid.addWidget(self.project_combo, 0, 1)
        sel_grid.addWidget(QLabel("版本"), 0, 2)
        sel_grid.addWidget(self.version_combo, 0, 3)
        sel_grid.addWidget(self.new_project_button, 0, 4)
        sel_grid.setColumnStretch(1, 1)
        sel_grid.setColumnStretch(3, 1)
        layout.addWidget(selector)

        # -- 元信息 ----------------------------------------------------
        meta = QGroupBox("基线信息")
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

        # -- 操作 ------------------------------------------------------
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.set_active_button = QPushButton("设为活跃")
        self.set_active_button.setToolTip("把选中的版本设为该项目出图时使用的活跃版本")
        self.set_active_button.clicked.connect(self._set_active)
        self.new_draft_button = QPushButton("新建草稿")
        self.new_draft_button.setToolTip("从当前选中版本派生一个新草稿（追加式，链 parent_version）")
        self.new_draft_button.clicked.connect(self._new_draft)
        self.validate_button = QPushButton("校验")
        self.validate_button.clicked.connect(self._validate)
        self.merge_button = QPushButton("上传文档合并")
        self.merge_button.setToolTip("上传新的项目介绍/背景文档，自动分析并把新信息合并为一个新草稿（需人工审校）")
        self.merge_button.clicked.connect(self._upload_and_merge)
        self.publish_button = QPushButton("发布")
        self.publish_button.setToolTip("把草稿发布为不可变版本并设为活跃（需通过结构校验与治理检查）")
        self.publish_button.clicked.connect(self._publish)
        self.open_json_button = QPushButton("打开 JSON")
        self.open_json_button.clicked.connect(self._open_json)
        self.reload_button = QPushButton("刷新")
        self.reload_button.clicked.connect(self.reload)
        for btn in (
            self.set_active_button,
            self.new_draft_button,
            self.merge_button,
            self.validate_button,
            self.publish_button,
            self.open_json_button,
            self.reload_button,
        ):
            btn.setMinimumWidth(0)
            actions.addWidget(btn)
        actions.addStretch(1)
        self.status_hint = QLabel("")
        self.status_hint.setObjectName("Subtitle")
        actions.addWidget(self.status_hint)
        layout.addLayout(actions)

        # -- 只读结构化预览 --------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._sections_container = QWidget()
        self._sections_layout = QVBoxLayout(self._sections_container)
        self._sections_layout.setContentsMargins(0, 0, 8, 0)
        self._sections_layout.setSpacing(12)
        scroll.setWidget(self._sections_container)
        layout.addWidget(scroll, 1)

        self.reload()

    # -- selection state ----------------------------------------------
    def _current_project(self) -> Optional[str]:
        return self.project_combo.currentData()

    def _current_version(self) -> Optional[str]:
        return self.version_combo.currentData()

    def reload(self) -> None:
        repo = baseline_service.repository()
        active_project = baseline_service.active_project_id()
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for info in repo.list_projects():
            label = f"{info.name}（{info.baseline_id}）"
            self.project_combo.addItem(label, info.baseline_id)
        if active_project:
            idx = self.project_combo.findData(active_project)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        self._reload_versions()

    def _reload_versions(self) -> None:
        repo = baseline_service.repository()
        project_id = self._current_project()
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        active_version = None
        if project_id:
            info = repo.get_project(project_id)
            active_version = info.active_version if info else None
            for version in reversed(repo.list_versions(project_id)):
                try:
                    data = repo.load_version(project_id, version)
                    status = _STATUS_LABELS.get(str(data.get("status")), str(data.get("status")))
                except BaselineError:
                    status = "?"
                marker = " ★活跃" if version == active_version else ""
                self.version_combo.addItem(f"{version} · {status}{marker}", version)
        self.version_combo.blockSignals(False)
        if self.version_combo.count():
            self.version_combo.setCurrentIndex(0)
        self._render_selected()

    def _new_project(self) -> None:
        dialog = NewProjectDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.generate_request:
            self._start_generate_project(dialog.generate_request)
            return
        if not dialog.created_id:
            return
        self.projectChanged.emit()  # 新项目已设为活跃
        self.reload()
        self._select_project(dialog.created_id)
        QMessageBox.information(
            self,
            "已创建项目",
            f"项目「{dialog.created_id}」已创建为草稿并设为活跃。"
            "可通过“上传文档合并”补充内容，校验通过后发布。",
        )

    def _select_project(self, baseline_id: str) -> None:
        idx = self.project_combo.findData(baseline_id)
        if idx >= 0:
            self.project_combo.setCurrentIndex(idx)

    # -- 从文档生成新项目（异步）--------------------------------------
    def _start_generate_project(self, req: dict) -> None:
        if not api_config.has_api_key():
            if QMessageBox.question(
                self, "尚未配置 API",
                "从文档生成基线需要调用 API，但尚未配置 API Key。是否现在去“设置”填写？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            ) == QMessageBox.StandardButton.Yes:
                SettingsDialog(self).exec()
            return
        self._gen_req = req
        self._gen_signals = GenerateSignals(self)
        self._gen_signals.done.connect(self._on_generate_ready)
        self._gen_signals.failed.connect(self._on_generate_failed)
        self.new_project_button.setEnabled(False)
        self._gen_progress = QProgressDialog("正在分析文档并生成新基线…", None, 0, 0, self)
        self._gen_progress.setWindowTitle("从文档生成基线")
        self._gen_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._gen_progress.setMinimumDuration(0)
        self._gen_progress.setCancelButton(None)
        self._gen_progress.show()
        run_generate_job(
            req["path"], baseline_service.bundled_baseline(),
            req["baseline_id"], req["name"],
            api_config.load_base_url(), api_config.load_api_key(),
            self._gen_signals, api_config.load_baseline_model(),
        )

    def _on_generate_failed(self, message: str) -> None:
        if getattr(self, "_gen_progress", None):
            self._gen_progress.close()
        self.new_project_button.setEnabled(True)
        QMessageBox.warning(self, "生成失败", message)

    def _on_generate_ready(self, payload) -> None:  # type: ignore[no-untyped-def]
        if getattr(self, "_gen_progress", None):
            self._gen_progress.close()
        self.new_project_button.setEnabled(True)
        skeleton, report = payload
        # 有候选则先审校 C 端/B 端文案；无候选也可直接用骨架（含定位/模块）创建
        if report.changes:
            if MergeReviewDialog(report, self).exec() != QDialog.DialogCode.Accepted:
                return
        draft = generate_mod.finalize(skeleton, report)
        try:
            info = baseline_service.create_project(draft)
        except BaselineError as exc:
            QMessageBox.warning(self, "创建失败", f"生成的基线未通过校验/治理：{exc}")
            return
        # 归档源文档
        try:
            baseline_service.repository().add_document(info.baseline_id, self._gen_req["path"])
        except BaselineError:
            pass
        self.projectChanged.emit()
        self.reload()
        self._select_project(info.baseline_id)
        QMessageBox.information(
            self, "已从文档生成项目",
            f"项目「{info.baseline_id}」已生成为草稿并设为活跃（采纳 {len(report.accepted_changes())} 条候选）。"
            "请检查定位/受众/课程体系与文案，校验通过后发布。",
        )

    def _on_project_changed(self) -> None:
        project_id = self._current_project()
        if project_id:
            baseline_service.set_active_project(project_id)
            self.projectChanged.emit()
        self._reload_versions()

    def _on_version_changed(self) -> None:
        self._render_selected()

    # -- rendering -----------------------------------------------------
    def _render_selected(self) -> None:
        self._clear_sections()
        project_id = self._current_project()
        version = self._current_version()
        if not project_id or not version:
            self._set_meta("（无项目）", "-", "-", "-")
            self._toggle_actions(None)
            return
        try:
            payload = baseline_service.repository().load_version(project_id, version)
        except BaselineError as exc:
            self._set_meta("读取失败", "-", "-", "-")
            self._add_text_section("错误", str(exc))
            self._toggle_actions(None)
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
        active_version = baseline_service.repository().active_version(project_id)
        self.status_hint.setText(
            f"活跃版本：{active_version or '无'} · 加载 {datetime.now():%H:%M:%S}"
        )
        self._toggle_actions(payload)
        self._build_sections(payload)

    def _toggle_actions(self, payload: "dict | None") -> None:
        is_draft = bool(payload) and payload.get("status") == "draft"
        version = self._current_version()
        active = baseline_service.repository().active_version(self._current_project() or "")
        self.publish_button.setEnabled(is_draft)
        self.set_active_button.setEnabled(bool(version) and version != active)
        self.new_draft_button.setEnabled(bool(payload))
        self.validate_button.setEnabled(bool(payload))
        self.open_json_button.setEnabled(bool(payload))

    # -- lifecycle actions --------------------------------------------
    def _set_active(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if not project_id or not version:
            return
        try:
            baseline_service.repository().set_active_version(project_id, version)
        except BaselineError as exc:
            QMessageBox.warning(self, "设置失败", str(exc))
            return
        self.projectChanged.emit()
        self._reload_versions()

    def _new_draft(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if not project_id or not version:
            return
        repo = baseline_service.repository()
        try:
            draft = repo.new_draft(project_id, version)
            new_version = repo.save_draft(draft)
        except BaselineError as exc:
            QMessageBox.warning(self, "新建草稿失败", str(exc))
            return
        self._reload_versions()
        idx = self.version_combo.findData(new_version)
        if idx >= 0:
            self.version_combo.setCurrentIndex(idx)
        QMessageBox.information(
            self,
            "已新建草稿",
            f"已从 {version} 新建草稿 {new_version}。\n可通过“打开 JSON”编辑或用文档合并功能补充内容，校验通过后发布。",
        )

    def _validate(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if not project_id or not version:
            return
        payload = baseline_service.repository().load_version(project_id, version)
        errors = validation_errors(payload)
        gov = governance.governance_issues(payload)
        if not errors and not gov:
            QMessageBox.information(self, "校验通过", "结构校验与治理检查均通过。")
            return
        lines = []
        if errors:
            lines.append("结构校验错误：\n- " + "\n- ".join(errors[:20]))
        if gov:
            lines.append("治理问题：\n- " + "\n- ".join(gov))
        QMessageBox.warning(self, "校验未通过", "\n\n".join(lines))

    def _publish(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if not project_id or not version:
            return
        confirm = QMessageBox.question(
            self,
            "确认发布",
            f"发布 {version} 后该版本不可再修改，并会成为该项目的活跃版本。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            baseline_service.repository().publish(project_id, version)
        except BaselineError as exc:
            QMessageBox.warning(self, "发布失败", str(exc))
            return
        self.projectChanged.emit()
        self._reload_versions()
        QMessageBox.information(self, "已发布", f"{version} 已发布并设为活跃版本。")

    def _open_json(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if project_id and version:
            open_path(self, baseline_service.repository().version_path(project_id, version))

    # -- document upload + LLM merge ----------------------------------
    def _upload_and_merge(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if not project_id or not version:
            return
        if not api_config.has_api_key():
            reply = QMessageBox.question(
                self,
                "尚未配置 API",
                "文档分析需要调用图像/文本 API，但尚未配置 API Key。是否现在去“设置”填写？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                SettingsDialog(self).exec()
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择项目介绍/背景文档", "",
            "文档 (*.pdf *.docx *.txt *.md);;All Files (*)",
        )
        if not path:
            return
        self._merge_project = project_id  # 捕获发起时的项目，回调不再重读下拉
        self._merge_source_path = Path(path)
        self._merge_current = baseline_service.repository().load_version(project_id, version)
        self._merge_signals = MergeSignals(self)
        self._merge_signals.done.connect(self._on_merge_ready)
        self._merge_signals.failed.connect(self._on_merge_failed)
        self.merge_button.setEnabled(False)  # 防止在途重复点击覆盖状态
        self._merge_progress = QProgressDialog("正在分析文档并生成合并建议…", None, 0, 0, self)
        self._merge_progress.setWindowTitle("文档合并")
        self._merge_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._merge_progress.setMinimumDuration(0)
        self._merge_progress.setCancelButton(None)
        self._merge_progress.show()
        run_merge_job(
            self._merge_source_path,
            self._merge_current,
            api_config.load_base_url(),
            api_config.load_api_key(),
            self._merge_signals,
            api_config.load_baseline_model(),
        )

    def _on_merge_failed(self, message: str) -> None:
        if getattr(self, "_merge_progress", None):
            self._merge_progress.close()
        self.merge_button.setEnabled(True)
        QMessageBox.warning(self, "文档分析失败", message)

    def _on_merge_ready(self, report: "merge.MergeReport") -> None:
        if getattr(self, "_merge_progress", None):
            self._merge_progress.close()
        self.merge_button.setEnabled(True)
        if not report.changes:
            QMessageBox.information(self, "无新增内容", "未从该文档中提取到可合并的新信息。")
            return
        dialog = MergeReviewDialog(report, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not report.accepted_changes() and not report.new_document.get("document_id"):
            QMessageBox.information(self, "未采纳任何内容", "没有采纳任何候选，未生成草稿。")
            return
        repo = baseline_service.repository()
        project_id = self._merge_project  # 用发起时捕获的项目，避免中途切换错配
        draft = merge.apply_report(
            self._merge_current, report, today_str(), repo.list_versions(project_id)
        )
        try:
            new_version = repo.save_draft(draft)
            repo.add_document(project_id, self._merge_source_path)
        except BaselineError as exc:
            QMessageBox.warning(self, "生成草稿失败", str(exc))
            return
        # 若合并期间切到了别的项目，切回发起项目再刷新版本列表
        if self._current_project() != project_id:
            baseline_service.set_active_project(project_id)
            self.reload()
        else:
            self._reload_versions()
        idx = self.version_combo.findData(new_version)
        if idx >= 0:
            self.version_combo.setCurrentIndex(idx)
        QMessageBox.information(
            self,
            "已生成合并草稿",
            f"已根据文档生成草稿 {new_version}（采纳 {len(report.accepted_changes())} 条）。"
            "请校验并在确认无误后发布。",
        )

    # -- preview rendering (read-only) --------------------------------
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
                widget.setParent(None)
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
            ("课程模块", [str(i) for i in consumer.get("course_modules", []) if str(i).strip()]),
            ("推荐画面", [str(i) for i in visual.get("recommended_scenes", []) if str(i).strip()]),
            ("构图规则", [str(i) for i in visual.get("composition_rules", []) if str(i).strip()]),
        ):
            if values:
                self._add_list_section(title, values)
        blocked = [str(i) for i in consumer.get("blocked_keywords", []) if str(i).strip()]
        if blocked:
            self._add_chip_section("禁止进入 C 端海报的关键词", blocked, "blocked")
        negative = [str(i) for i in prompt_policy.get("negative_constraints", []) if str(i).strip()]
        if negative:
            self._add_chip_section("文生图负面约束", negative, "negative")
        self._sections_layout.addStretch(1)

    def _add_text_section(self, title: str, text: str) -> None:
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box_layout.addWidget(label)
        self._sections_layout.addWidget(box)

    def _add_list_section(self, title: str, values: "list[str]") -> None:
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(6)
        for value in values:
            label = QLabel(f"•  {value}")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box_layout.addWidget(label)
        self._sections_layout.addWidget(box)

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
