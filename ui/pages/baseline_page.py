"""Multi-project baseline manager: project/version selection, lifecycle, preview.

The structured preview stays read-only; version lifecycle (new draft / validate
/ publish / set-active) runs through the Qt-free repository so governance and
append-only versioning are enforced in one place.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
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
from ui import api_config, baseline_service, theme
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
    # 后台加载 overview 完成/失败后回主线程（跨线程 emit -> Qt 自动 QueuedConnection）。
    _overviewReady = Signal(object, int)
    _overviewFailed = Signal(str, int)

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # -- 项目 / 版本选择 -------------------------------------------
        selector = QGroupBox("项目与版本")
        sel_grid = QGridLayout(selector)
        sel_grid.setHorizontalSpacing(theme.SPACE_M)
        sel_grid.setVerticalSpacing(theme.SPACE_S)
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self.version_combo = QComboBox()
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        self.new_project_button = QPushButton("新建项目…")
        self.new_project_button.setToolTip("从内置模板/复制现有项目/导入 JSON 新建一个项目基线")
        self.new_project_button.clicked.connect(self._new_project)
        self.use_project_button = QPushButton("设为当前项目")
        self.use_project_button.setToolTip("文生图等工作流将改用该项目的活跃基线；仅浏览无需此操作")
        self.use_project_button.setObjectName("SecondaryButton")
        self.use_project_button.clicked.connect(self._use_project)
        # 两行布局：单行塞 2 组下拉 + 2 个按钮在窄窗口会把按钮压成单字。
        sel_grid.addWidget(QLabel("项目"), 0, 0)
        sel_grid.addWidget(self.project_combo, 0, 1)
        sel_grid.addWidget(self.use_project_button, 0, 2)
        sel_grid.addWidget(QLabel("版本"), 1, 0)
        sel_grid.addWidget(self.version_combo, 1, 1)
        sel_grid.addWidget(self.new_project_button, 1, 2)
        sel_grid.setColumnStretch(1, 1)
        layout.addWidget(selector)

        # -- 元信息 ----------------------------------------------------
        meta = QGroupBox("基线信息")
        meta_layout = QGridLayout(meta)
        meta_layout.setHorizontalSpacing(theme.SPACE_M)
        meta_layout.setVerticalSpacing(theme.SPACE_S)
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
        # 7 个按钮用 FlowLayout：窗口窄时自动换行，而不是把中文按钮压成省略号。
        actions_container = QWidget()
        actions = FlowLayout(actions_container, margin=0, spacing=6)
        self.set_active_button = QPushButton("设为活跃")
        self.set_active_button.setToolTip("把选中的版本设为该项目出图时使用的活跃版本")
        self.set_active_button.setObjectName("SecondaryButton")
        self.set_active_button.clicked.connect(self._set_active)
        self.new_draft_button = QPushButton("新建草稿")
        self.new_draft_button.setToolTip("以当前选中版本为底稿生成一个可编辑的新草稿，原版本保持不变")
        self.new_draft_button.clicked.connect(self._new_draft)
        self.validate_button = QPushButton("校验")
        self.validate_button.clicked.connect(self._validate)
        self.merge_button = QPushButton("用文档补充内容")
        self.merge_button.setToolTip("上传项目介绍/背景文档，自动提取新信息，生成一个待人工审校的新草稿")
        self.merge_button.clicked.connect(self._upload_and_merge)
        self.publish_button = QPushButton("发布")
        self.publish_button.setToolTip("把草稿发布为不可变版本并设为活跃（需通过结构校验与治理检查）")
        self.publish_button.setObjectName("DangerButton")  # 不可逆操作，视觉上警示
        self.publish_button.clicked.connect(self._publish)
        self.open_json_button = QPushButton("打开源文件")
        self.open_json_button.setToolTip("在系统中打开该版本的底层 JSON 数据文件（高级功能）")
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
        layout.addWidget(actions_container)
        hint_row = QHBoxLayout()
        hint_row.setSpacing(6)
        # 加载态的旋转指示：纯文字"加载中"会让人以为界面卡死。
        self._loading_icon = qta.IconWidget()
        self._loading_icon.setIconSize(QSize(14, 14))
        self._loading_icon.hide()
        self.status_hint = QLabel("")
        self.status_hint.setObjectName("Subtitle")
        self.status_hint.setWordWrap(True)
        hint_row.addWidget(self._loading_icon, 0, Qt.AlignmentFlag.AlignTop)
        hint_row.addWidget(self.status_hint, 1)
        layout.addLayout(hint_row)

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

        # 异步加载：切换/刷新时后台拉 overview，seq 保证只应用最新一次结果。
        self._overview_seq = 0
        self._global_active_project: "str | None" = None
        self._overviewReady.connect(self._apply_overview)
        self._overviewFailed.connect(self._on_overview_failed)
        baseline_service.repository()  # 主线程预热单例，避免多后台线程首次并发初始化
        self.reload()

    def _notify(self, kind: str, text: str, timeout_ms: int = 8000) -> None:
        """瞬时结果（成功/提示）走主窗口的非阻塞 banner，与运行反馈一致；
        需要用户决策的确认与含长列表的失败仍用模态框。"""
        banner = getattr(self.window(), "banner", None)
        if banner is not None:
            banner.show_message(kind, text, timeout_ms=timeout_ms)
        else:  # 独立实例化（如测试）时退回模态
            QMessageBox.information(self, "提示", text)

    # -- selection state ----------------------------------------------
    def _current_project(self) -> Optional[str]:
        return self.project_combo.currentData()

    def _current_version(self) -> Optional[str]:
        return self.version_combo.currentData()

    def reload(self) -> None:
        """Refresh keeping the current selection (async; never blocks the UI)."""
        self._refresh(self._current_project(), self._current_version())

    def _refresh(
        self, selected_project: Optional[str] = None, selected_version: Optional[str] = None
    ) -> None:
        """Load the overview off the UI thread; only the latest request is applied."""
        self._overview_seq += 1
        seq = self._overview_seq
        self.status_hint.setText("正在加载…")
        self._loading_icon.setIcon(
            qta.icon(
                "mdi6.loading",
                color=theme.current_tokens()["accent"],
                animation=qta.Spin(self._loading_icon),
            )
        )
        self._loading_icon.show()

        def worker() -> None:
            try:
                overview = baseline_service.load_overview(selected_project, selected_version)
            except Exception as exc:  # noqa: BLE001 — surface any load error on the UI thread
                self._overviewFailed.emit(str(exc), seq)
                return
            self._overviewReady.emit(overview, seq)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_overview(self, overview: "baseline_service.BaselineOverview", seq: int) -> None:
        if seq != self._overview_seq:
            return  # 过期结果（用户已再次切换），丢弃
        self._loading_icon.hide()
        self._global_active_project = overview.global_active_project
        self.project_combo.blockSignals(True)
        self.version_combo.blockSignals(True)
        self.project_combo.clear()
        for info in overview.projects:
            marker = " · 当前" if info.baseline_id == overview.global_active_project else ""
            self.project_combo.addItem(f"{info.name}（{info.baseline_id}）{marker}", info.baseline_id)
        if overview.active_project_id:
            idx = self.project_combo.findData(overview.active_project_id)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.version_combo.clear()
        star = qta.icon("mdi6.star", color=theme.current_tokens()["accent"])
        for summary in reversed(overview.versions):  # 最新版本显示在最上（overview.versions 为升序）
            status = _STATUS_LABELS.get(summary.status, summary.status)
            if summary.version == overview.active_version:
                # 活跃版本用星形图标标注（文字符号 ★ 随系统字体渲染不一）
                self.version_combo.addItem(star, f"{summary.version} · {status} · 活跃", summary.version)
            else:
                self.version_combo.addItem(f"{summary.version} · {status}", summary.version)
        if overview.selected_version:
            idx = self.version_combo.findData(overview.selected_version)
            if idx >= 0:
                self.version_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        self.version_combo.blockSignals(False)
        self._render_overview(overview)

    def _on_overview_failed(self, message: str, seq: int) -> None:
        if seq != self._overview_seq:
            return
        self._loading_icon.hide()
        self._clear_sections()
        self._set_meta("加载失败", "-", "-", "-")
        self._add_text_section("错误", message)
        self._toggle_actions(None, None)
        self.status_hint.setText("加载失败")

    def _render_overview(self, overview: "baseline_service.BaselineOverview") -> None:
        self._clear_sections()
        payload = overview.selected_payload
        if not overview.active_project_id or not overview.selected_version or payload is None:
            self._set_meta("（无项目）", "-", "-", "-")
            self._toggle_actions(None, overview.active_version)
            self.status_hint.setText("")
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
        self.status_hint.setText(f"活跃版本：{overview.active_version or '无'}")
        self._toggle_actions(payload, overview.active_version)
        self._build_sections(payload)

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
        self._refresh(selected_project=dialog.created_id)
        self._notify(
            "success",
            f"项目「{dialog.created_id}」已创建为草稿并设为活跃，"
            "可通过“用文档补充内容”完善信息，校验通过后发布。",
        )

    # -- 从文档生成新项目（异步）--------------------------------------
    def _start_generate_project(self, req: dict) -> None:
        if not api_config.has_api_key():
            if QMessageBox.question(
                self, "API 尚未就绪",
                f"从文档生成基线需要调用 API。{api_config.missing_key_message()}\n\n是否打开“设置”查看？",
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
        self._refresh(selected_project=info.baseline_id)
        self._notify(
            "success",
            f"项目「{info.baseline_id}」已生成为草稿并设为活跃（采纳 {len(report.accepted_changes())} 条候选）。"
            "请检查定位/受众/课程体系与文案，校验通过后发布。",
        )

    def _on_project_changed(self) -> None:
        # 下拉切换只是"浏览查看"，不改工作流实际使用的当前项目——
        # 生效需显式点"设为当前项目"（选择 ≠ 生效）。
        self._refresh(selected_project=self._current_project())

    def _on_version_changed(self) -> None:
        self._refresh(self._current_project(), self._current_version())

    def _use_project(self) -> None:
        project_id = self._current_project()
        if not project_id or project_id == self._global_active_project:
            return
        confirm = QMessageBox.question(
            self,
            "设为当前项目",
            f"将「{project_id}」设为当前项目？\n文生图等工作流将改用该项目的活跃基线。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        baseline_service.set_active_project(project_id)
        self.projectChanged.emit()
        self._refresh(project_id, self._current_version())

    # -- rendering -----------------------------------------------------
    def _toggle_actions(self, payload: "dict | None", active_version: "str | None") -> None:
        is_draft = bool(payload) and payload.get("status") == "draft"
        version = self._current_version()
        project = self._current_project()
        self.publish_button.setEnabled(is_draft)
        self.set_active_button.setEnabled(bool(version) and version != active_version)
        self.use_project_button.setEnabled(bool(project) and project != self._global_active_project)
        self.new_draft_button.setEnabled(bool(payload))
        self.validate_button.setEnabled(bool(payload))
        self.open_json_button.setEnabled(bool(payload))

    # -- lifecycle actions --------------------------------------------
    def _set_active(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if not project_id or not version:
            return
        # 与"发布"一致：改变出图行为的状态操作需确认，避免误点静默生效。
        confirm = QMessageBox.question(
            self,
            "设为活跃版本",
            f"把 {version} 设为项目「{project_id}」出图使用的活跃版本？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            baseline_service.repository().set_active_version(project_id, version)
        except BaselineError as exc:
            QMessageBox.warning(self, "设置失败", str(exc))
            return
        self.projectChanged.emit()
        self._refresh(project_id, version)

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
        self._refresh(project_id, new_version)
        self._notify(
            "success",
            f"已从 {version} 新建草稿 {new_version}，可用“用文档补充内容”完善信息，校验通过后发布。",
        )

    def _validate(self) -> None:
        project_id, version = self._current_project(), self._current_version()
        if not project_id or not version:
            return
        payload = baseline_service.repository().load_version(project_id, version)
        errors = validation_errors(payload)
        gov = governance.governance_issues(payload)
        if not errors and not gov:
            self._notify("success", "校验通过：结构校验与治理检查均通过。")
            return
        lines = []
        if errors:
            lines.append("结构校验错误：\n- " + "\n- ".join(errors[:20]))
        if gov:
            lines.append("内容合规提醒：\n- " + "\n- ".join(gov))
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
        self._refresh(project_id, version)
        self._notify("success", f"{version} 已发布并设为活跃版本。")

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
                "API 尚未就绪",
                f"文档分析需要调用文本 API。{api_config.missing_key_message()}\n\n是否打开“设置”查看？",
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
            self._notify("info", "未从该文档中提取到可合并的新信息。")
            return
        dialog = MergeReviewDialog(report, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not report.accepted_changes() and not report.new_document.get("document_id"):
            self._notify("info", "没有采纳任何候选，未生成草稿。")
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
        self._refresh(project_id, new_version)
        self._notify(
            "success",
            f"已根据文档生成草稿 {new_version}（采纳 {len(report.accepted_changes())} 条），"
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
