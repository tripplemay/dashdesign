"""Main window and application factory for the DashDesign desktop client."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QLibraryInfo,
    QProcess,
    QProcessEnvironment,
    QPropertyAnimation,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QTranslator,
    QUrl,
    qVersion,
)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)

from app_runtime import (
    APP_VERSION,
    IMAGE_EXTENSIONS,
    PROJECT_ROOT,
    app_icon_path,
    configured_update_manifest_url,
    first_output_image,
    platform_key,
    runtime_root,
    version_tuple,
)
from ui import cloud_bootstrap, commands, installer, theme
from ui.pages import BaselinePage, BatchPage, GptPage, QrPage, TextImagePage
from ui.progress import ProgressModel, parse_progress_line
from ui.updater import (
    DownloadSignals,
    UpdateSignals,
    download_update,
    fetch_update_manifest,
)
from update_core import UpdateInfo, evaluate_manifest
from ui.utils import friendly_error_hint, open_path
from ui.widgets import ImagePreview, InfoBanner, ProgressPanel, SettingsDialog


class DashDesignQtApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        # Zero-config bootstrap: refresh the shared cloud config (image API creds,
        # baseline endpoint) in the background so ordinary users set nothing.
        cloud_bootstrap.bootstrap_async()
        self.setWindowTitle(f"DashDesign 印刷图片工作流 · v{APP_VERSION}")
        _icon = app_icon_path()
        if _icon.exists():
            self.setWindowIcon(QIcon(str(_icon)))
        # 最小高度须容得下 1080p@150%（逻辑 720，扣任务栏/标题栏约剩 650）与
        # 1366x768 笔记本；初始尺寸按屏幕可用区域钳制，避免首启即超出屏幕。
        self.setMinimumSize(960, 560)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            self.resize(min(1180, available.width() - 40), min(780, available.height() - 60))
        else:
            self.resize(1180, 780)
        self.process: "QProcess | None" = None
        self.last_output_dir: "Path | None" = None
        self.current_preview_path: "Path | None" = None
        self._running = False
        self._running_title = ""  # 正在运行的工作流名，进度/状态栏标注归属
        self._stderr_tail: "deque[str]" = deque(maxlen=5)
        self._progress = ProgressModel()
        self._captured: "list[str]" = []
        self._stdout_buf = ""
        self._elapsed = QElapsedTimer()
        self._elapsed_tick = QTimer(self)
        self._elapsed_tick.setInterval(1000)
        self._elapsed_tick.timeout.connect(self._update_elapsed_status)
        self.update_signals = UpdateSignals(self)
        self.update_signals.result.connect(self.handle_update_result)
        self.update_signals.error.connect(self.handle_update_error)
        self.download_signals = DownloadSignals(self)
        self.download_signals.progress.connect(self._on_update_progress)
        self.download_signals.done.connect(self._on_update_downloaded)
        self.download_signals.error.connect(self._on_update_error)
        self.download_signals.cancelled.connect(self._on_update_cancelled)
        self._update_dialog: "QProgressDialog | None" = None
        self._update_cancelled = False
        self._page_anim: "QPropertyAnimation | None" = None

        self._build_actions()
        self._build_menu()
        self._build_ui()
        self._apply_icons()
        if theme.manager() is not None:
            theme.manager().changed.connect(self._on_theme_changed)
        self._restore_settings()
        self.statusBar().showMessage("就绪")
        # 更新地址可来自 baked 文件或 app-config 下发的 update_manifest_url，任一存在即启动静默检查。
        if configured_update_manifest_url() or cloud_bootstrap.cached_app_config().get("update_manifest_url"):
            QTimer.singleShot(1600, lambda: self.check_for_updates(silent=True))

    def _build_actions(self) -> None:
        self.run_action = QAction("运行当前工作流", self)
        # Ctrl 在 macOS 上由 Qt 自动映射为 Cmd；Meta 反而是 Ctrl/Win 键。
        self.run_action.setShortcut(QKeySequence("Ctrl+R"))
        self.run_action.triggered.connect(self.run_current)

        self.stop_action = QAction("停止", self)
        self.stop_action.setShortcut(QKeySequence("Ctrl+."))
        self.stop_action.triggered.connect(self.stop_process)
        self.stop_action.setEnabled(False)

        self.open_project_action = QAction("打开项目目录", self)
        self.open_project_action.triggered.connect(lambda: open_path(self, PROJECT_ROOT))

        self.open_output_action = QAction("打开最近输出", self)
        self.open_output_action.triggered.connect(self.open_last_output)

        self.export_log_action = QAction("导出运行日志…", self)
        self.export_log_action.triggered.connect(self.export_log)

        self.settings_action = QAction("设置…", self)
        self.settings_action.setShortcut(QKeySequence.StandardKey.Preferences)
        self.settings_action.triggered.connect(self.open_settings)

        self.check_update_action = QAction("检查更新", self)
        self.check_update_action.triggered.connect(lambda: self.check_for_updates(silent=False))

        self.quit_action = QAction("退出", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.close)

    def _build_menu(self) -> None:
        self.menuBar().setNativeMenuBar(True)
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self.settings_action)
        file_menu.addAction(self.open_project_action)
        file_menu.addAction(self.open_output_action)
        file_menu.addAction(self.export_log_action)
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)

        workflow_menu = self.menuBar().addMenu("工作流")
        workflow_menu.addAction(self.run_action)
        workflow_menu.addAction(self.stop_action)

        view_menu = self.menuBar().addMenu("视图")
        appearance_menu = view_menu.addMenu("外观")
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        current_mode = theme.manager().mode() if theme.manager() is not None else "system"
        self._theme_actions: "dict[str, QAction]" = {}
        for mode, label in (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(mode == current_mode)
            action.triggered.connect(lambda checked=False, m=mode: self._set_theme_mode(m))
            self.theme_action_group.addAction(action)
            appearance_menu.addAction(action)
            self._theme_actions[mode] = action

        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction(self.check_update_action)
        about = QAction("关于 DashDesign", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFixedWidth(196)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setSpacing(2)
        self.nav.setIconSize(QSize(18, 18))
        for key, label in [
            ("baseline", "项目基线"),
            ("text-image", "文生图"),
            ("batch", "批量印刷"),
            ("gpt", "图片修改"),
            ("qr", "去二维码留空"),
        ]:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(QSize(170, 42))
            self.nav.addItem(item)
        self.nav.currentRowChanged.connect(self.switch_workflow)
        root_layout.addWidget(self.nav)

        work_area = QWidget()
        work_layout = QVBoxLayout(work_area)
        work_layout.setContentsMargins(18, 16, 18, 16)
        work_layout.setSpacing(12)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        self.title_label = QLabel("批量印刷")
        self.title_label.setObjectName("Title")
        self.subtitle_label = QLabel("将根目录或指定目录中的图片输出为印刷规格。")
        self.subtitle_label.setObjectName("Subtitle")
        self.subtitle_label.setWordWrap(True)  # 长副标题在窄窗口换行而不是撑宽 header
        title_block.addWidget(self.title_label)
        title_block.addWidget(self.subtitle_label)
        header.addLayout(title_block, 1)
        self.run_button = self._button("运行", self.run_current, primary=True)
        self.run_button.setToolTip("运行当前工作流（Ctrl+R，macOS 为 ⌘R）")
        self.stop_button = self._button("停止", self.stop_process)
        self.stop_button.setObjectName("SecondaryButton")
        self.stop_button.setToolTip("停止正在运行的工作流（Ctrl+.）")
        self.open_output_button = self._button("打开输出", self.open_last_output)
        header.addWidget(self.run_button)
        header.addWidget(self.stop_button)
        header.addWidget(self.open_output_button)
        work_layout.addLayout(header)

        self.banner = InfoBanner()
        work_layout.addWidget(self.banner)

        self.baseline_page = BaselinePage()
        self.text_image_page = TextImagePage()
        self.batch_page = BatchPage()
        self.gpt_page = GptPage()
        self.qr_page = QrPage()
        self.pages = [
            self.baseline_page,
            self.text_image_page,
            self.batch_page,
            self.gpt_page,
            self.qr_page,
        ]
        # CI 冒烟测试通过 window.t2i_prompt 填写提示词，保持该属性可用。
        self.t2i_prompt = self.text_image_page.t2i_prompt

        # 源路径变化时自动刷新预览（仅当对应页面处于前台）。
        self.batch_page.batch_input.edit.textChanged.connect(lambda: self._preview_if_current(2))
        self.batch_page.batch_only.textChanged.connect(lambda: self._preview_if_current(2))
        self.gpt_page.gpt_source.edit.textChanged.connect(lambda: self._preview_if_current(3))
        self.qr_page.qr_input.edit.textChanged.connect(lambda: self._preview_if_current(4))
        self.qr_page.select_button.toggled.connect(self._on_qr_select_toggled)
        # 切换/发布项目后，文生图页的活跃基线标签需刷新。
        self.baseline_page.projectChanged.connect(self.text_image_page.refresh_active_baseline)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        self.main_splitter.addWidget(self.stack)
        self.main_splitter.addWidget(self._make_preview_panel())
        self.main_splitter.setSizes([620, 420])
        # 禁止把表单页/预览面板拖到 0 宽——塌陷状态会被 saveState 持久化，
        # 下次启动看起来像"界面坏了"。
        self.main_splitter.setChildrenCollapsible(False)
        work_layout.addWidget(self.main_splitter, 1)

        self.progress_panel = ProgressPanel()
        work_layout.addWidget(self.progress_panel)

        root_layout.addWidget(work_area, 1)
        self.setCentralWidget(root)
        self.nav.setCurrentRow(0)

    def _button(self, label: str, slot, primary: bool = False) -> QPushButton:  # type: ignore[no-untyped-def]
        button = QPushButton(label)
        button.clicked.connect(slot)
        if primary:
            button.setObjectName("PrimaryButton")
        return button

    _NAV_ICON_NAMES = (
        "mdi6.clipboard-text-outline",
        "mdi6.image-edit-outline",
        "mdi6.printer-outline",
        "mdi6.auto-fix",
        "mdi6.qrcode-remove",
    )

    def _apply_icons(self) -> None:
        tokens = theme.current_tokens()
        nav_fg = tokens["nav_fg"]
        nav_selected = tokens["sidebar_item_selected_fg"]
        for row, icon_name in enumerate(self._NAV_ICON_NAMES):
            item = self.nav.item(row)
            if item is not None:
                item.setIcon(qta.icon(icon_name, color=nav_fg, color_selected=nav_selected))
        self.run_button.setIcon(qta.icon("mdi6.play", color=tokens["accent_fg"]))
        self.stop_button.setIcon(qta.icon("mdi6.stop", color=nav_fg))
        self.open_output_button.setIcon(qta.icon("mdi6.folder-open-outline", color=nav_fg))
        self.run_action.setIcon(qta.icon("mdi6.play", color=nav_fg))
        self.stop_action.setIcon(qta.icon("mdi6.stop", color=nav_fg))

    def _on_theme_changed(self, resolved: str) -> None:
        self._apply_icons()
        self._render_progress()
        # 让"视图 → 外观"菜单的勾选与设置对话框改动保持同步（不重触发 triggered）。
        manager = theme.manager()
        if manager is not None:
            action = self._theme_actions.get(manager.mode())
            if action is not None and not action.isChecked():
                action.setChecked(True)

    def _set_theme_mode(self, mode: str) -> None:
        if theme.manager() is not None:
            theme.manager().set_mode(mode)

    def _make_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("PreviewPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QHBoxLayout()
        title.addWidget(QLabel("图片预览"))
        title.addStretch(1)
        load_input = QPushButton("预览输入")
        load_input.clicked.connect(self.preview_input)
        load_output = QPushButton("预览输出")
        load_output.clicked.connect(self.preview_recent_output)
        title.addWidget(load_input)
        title.addWidget(load_output)
        layout.addLayout(title)

        self.preview_path_label = QLabel("拖入图片或点击预览输入")
        self.preview_path_label.setObjectName("Subtitle")
        self.preview_path_label.setWordWrap(True)
        layout.addWidget(self.preview_path_label)

        self.preview_info_label = QLabel("")
        self.preview_info_label.setObjectName("Subtitle")
        layout.addWidget(self.preview_info_label)

        self.preview = ImagePreview()
        self.preview.setObjectName("PreviewCanvas")
        self.preview.pathDropped.connect(self.handle_dropped_path)
        self.preview.selectionMade.connect(self._on_preview_selection)
        layout.addWidget(self.preview, 1)

        tools = QHBoxLayout()
        for label, slot in [
            ("适应", self.preview.fit_image),
            ("100%", self.preview.actual_size),
            ("放大", lambda: self.preview.zoom(1.2)),
            ("缩小", lambda: self.preview.zoom(1 / 1.2)),
        ]:
            button = QPushButton(label)
            button.clicked.connect(slot)
            tools.addWidget(button)
        tools.addStretch(1)
        layout.addLayout(tools)
        return panel

    # (标题, 副标题, 运行按钮文案；None = 本页无可运行工作流)
    _PAGE_TITLES = [
        ("项目基线", "管理与查看各项目的海报生成基线；本页无可运行任务，出图请到“文生图”。", None),
        ("文生图", "基于当前项目基线生成无文字背景或完整 Image2 海报。", "运行文生图"),
        ("批量印刷", "将根目录或指定目录中的图片输出为印刷规格。", "运行批量印刷"),
        ("图片修改", "上传一张图片，用 AI 按你的要求修改它。", "运行图片修改"),
        ("去二维码留空", "只清除指定二维码区域，后期手动添加二维码。", "运行去二维码"),
    ]

    def _fade_in_page(self, widget: QWidget) -> None:
        """切页 150ms 淡入；结束后移除 effect，避免常驻影响滚动渲染。"""
        if self._page_anim is not None:
            self._page_anim.stop()  # 触发上一个动画的清理
            self._page_anim = None
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _cleanup() -> None:
            widget.setGraphicsEffect(None)
            if self._page_anim is anim:
                self._page_anim = None

        anim.finished.connect(_cleanup)
        self._page_anim = anim
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def switch_workflow(self, row: int) -> None:
        if row < 0:
            return
        previous = self.stack.currentIndex()
        self.stack.setCurrentIndex(row)
        if previous != row:
            self._fade_in_page(self.stack.currentWidget())
        title, subtitle, run_label = self._PAGE_TITLES[row]
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        # 运行按钮写明会跑什么，用户不必猜"运行"作用于哪个工作流；
        # 基线页无可运行任务，直接隐藏而不是留一个费解的灰按钮。
        self.run_button.setVisible(run_label is not None)
        if run_label is not None:
            self.run_button.setText(run_label)
        if row != 4 and self.qr_page.select_button.isChecked():
            self.qr_page.select_button.setChecked(False)
        if row == 0:
            self.baseline_page.reload()
        elif row == 1:
            self.text_image_page.refresh_active_baseline()
        # 进度与提示都属于"上一次运行/上一个页面"，切换页面时清掉，
        # 避免在新工作流页面残留其他工作流的进度或提示。运行中不清（进度仍在进行）。
        # 例外：失败提示保留——用户常需切页排查（看设置/导日志），回来时错误信息不能消失。
        if not self._running:
            self.progress_panel.hide()
            if self.banner.kind() != "error":
                self.banner.dismiss()
                self.statusBar().showMessage("就绪")
        self._update_run_controls()
        self.preview_input()

    def _preview_if_current(self, row: int) -> None:
        if self.nav.currentRow() == row:
            self.preview_input()

    def _on_qr_select_toggled(self, checked: bool) -> None:
        if checked:
            if self.preview.pixmap_item is None:
                self.preview_input()
            if self.preview.pixmap_item is None:
                self.banner.show_message("warning", "请先选择输入图片，再在预览上框选。", timeout_ms=4000)
                self.qr_page.select_button.setChecked(False)
                return
        self.preview.set_selection_mode(checked)

    def _on_preview_selection(self, rect, size) -> None:  # type: ignore[no-untyped-def]
        if self.nav.currentRow() != 4:
            return
        self.qr_page.apply_selection(rect, size)
        self.qr_page.select_button.setChecked(False)
        self.banner.show_message(
            "success",
            f"已框选清除区域：{rect.x()},{rect.y()} ~ {rect.x() + rect.width()},{rect.y() + rect.height()}"
            f"（参考尺寸 {size.width()}x{size.height()}）",
            timeout_ms=4000,
        )

    def _capture(self, text: str) -> None:
        # 原始输出不再在界面显示，只保留在内存里供"导出运行日志"排查。
        timestamp = datetime.now().strftime("%H:%M:%S")
        for line in text.splitlines() or [text]:
            self._captured.append(f"[{timestamp}] {line}")

    def export_log(self) -> None:
        if not self._captured:
            self.banner.show_message("info", "还没有可导出的运行输出。", timeout_ms=3000)
            return
        default = str(Path.home() / f"dashdesign-log-{datetime.now():%Y%m%d-%H%M%S}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "导出运行日志", default, "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            Path(path).write_text("\n".join(self._captured) + "\n", encoding="utf-8")
        except OSError as exc:
            self.banner.show_message("error", f"日志导出失败：{exc}")
            return
        self.banner.show_message("success", f"运行日志已导出：{path}", timeout_ms=4000)

    def _update_run_controls(self) -> None:
        can_run = not self._running and self.nav.currentRow() != 0
        self.run_action.setEnabled(can_run)
        self.run_button.setEnabled(can_run)
        self.stop_action.setEnabled(self._running)
        self.stop_button.setEnabled(self._running)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._update_run_controls()
        if running:
            self._elapsed.start()
            self._elapsed_tick.start()
            self._update_elapsed_status()
        else:
            self._elapsed_tick.stop()

    def _elapsed_seconds(self) -> int:
        return self._elapsed.elapsed() // 1000 if self._elapsed.isValid() else 0

    def _elapsed_text(self) -> str:
        seconds = self._elapsed_seconds()
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _update_elapsed_status(self) -> None:
        # 每秒刷新：状态栏计时 + 进度面板（ETA/用时随之更新）。
        # 标注工作流名：运行中切到别的页面时，用户仍能看出是谁在跑。
        prefix = f"{self._running_title} " if self._running_title else ""
        self.statusBar().showMessage(f"{prefix}运行中 · {self._elapsed_text()}")
        self._render_progress()

    def _render_progress(self) -> None:
        if self._running:
            self.progress_panel.render(self._progress, self._elapsed_seconds(), self._running)

    def run_current(self) -> None:
        if self.process is not None:
            self.banner.show_message("info", "已有工作流正在运行，请先停止或等待完成。", timeout_ms=4000)
            return
        current_page = self.pages[self.nav.currentRow()] if self.nav.currentRow() >= 0 else None
        try:
            command, output_dir, env_updates = self.build_current_command()
        except ValueError as exc:
            self.banner.show_message("error", f"参数错误：{exc}")
            if current_page is not None and hasattr(current_page, "on_validation_error"):
                current_page.on_validation_error()
            return
        if current_page is not None and hasattr(current_page, "confirm_run"):
            if not current_page.confirm_run(self):
                return

        self.banner.dismiss()
        self._stderr_tail.clear()
        self._captured = []
        self._stdout_buf = ""
        self._progress = ProgressModel()
        self.progress_panel.reset()
        self.last_output_dir = output_dir
        self._running_title = self._PAGE_TITLES[self.nav.currentRow()][0]
        self._capture("$ " + " ".join(command))
        self._set_running(True)

        process_env = QProcessEnvironment.systemEnvironment()
        for key, value in env_updates.items():
            process_env.insert(key, value)
        # 让脚本发出结构化进度事件；命令行直跑脚本时不设此变量，输出保持原样。
        process_env.insert("DASHDESIGN_PROGRESS", "1")
        # 强制子进程 UTF-8 I/O：中文 Windows 默认 cp936，会让进度中文变乱码。
        # 与 app_runtime 里对 stdout/stderr 的显式 reconfigure 互为兜底，并覆盖
        # 脚本可能再拉起的嵌套 Python 子进程。
        process_env.insert("PYTHONUTF8", "1")
        process_env.insert("PYTHONIOENCODING", "utf-8")

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(runtime_root()))
        self.process.setProcessEnvironment(process_env)
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.start(command[0], command[1:])

    def process_error(self, error) -> None:  # type: ignore[no-untyped-def]
        # 启动失败时 finished 永不触发，必须在这里复位运行状态；
        # 其他错误（如 Crashed）仍由 process_finished 统一收尾。
        if error != QProcess.ProcessError.FailedToStart or self.process is None:
            return
        self._capture("[错误] 工作流进程启动失败：找不到可执行文件或没有执行权限。")
        self.statusBar().showMessage("启动失败")
        self.progress_panel.finalize(self._progress, False, self._elapsed_seconds())
        self.banner.show_message(
            "error", "工作流进程启动失败：找不到可执行文件或没有执行权限，请检查安装是否完整。"
        )
        process = self.process
        self.process = None
        process.deleteLater()
        self._set_running(False)

    def stop_process(self) -> None:
        if self.process is None:
            return
        self._capture("[停止] 已发送 terminate")
        self.process.terminate()
        if not self.process.waitForFinished(2500):
            self.process.kill()

    def read_stdout(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if not data:
            return
        self._stdout_buf += data
        while "\n" in self._stdout_buf:
            line, self._stdout_buf = self._stdout_buf.split("\n", 1)
            self._handle_output_line(line)

    def _handle_output_line(self, line: str) -> None:
        event = parse_progress_line(line)
        if event is not None:
            self._progress.apply(event)
            self._render_progress()
            return
        if line.strip():
            self._capture(line)

    def _flush_stdout_buf(self) -> None:
        if self._stdout_buf:
            self._handle_output_line(self._stdout_buf)
            self._stdout_buf = ""

    def read_stderr(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardError()).decode(errors="replace")
        if data:
            for line in data.rstrip().splitlines():
                if line.strip():
                    self._stderr_tail.append(line.strip())
            self._capture(data.rstrip())

    def process_finished(self, exit_code: int, exit_status) -> None:  # type: ignore[no-untyped-def]
        self._flush_stdout_buf()
        elapsed_seconds = self._elapsed_seconds()
        elapsed = self._elapsed_text()
        self._capture(f"[完成] exit={exit_code} · 用时 {elapsed}")
        success = exit_code == 0
        self.progress_panel.finalize(self._progress, success, elapsed_seconds)
        self._set_running(False)
        process = self.process
        self.process = None
        if process is not None:
            # 与 process_error 分支一致：释放已结束的 QProcess，避免多次运行累积。
            process.deleteLater()
        workflow = self._running_title or "工作流"
        if success:
            self.statusBar().showMessage(f"完成 · 用时 {elapsed}")
            self.banner.show_message(
                "success",
                f"{workflow}运行完成，用时 {elapsed}。",
                action_label="打开输出目录",
                action_callback=self.open_last_output,
                timeout_ms=8000,
            )
            self.preview_recent_output()
        else:
            self.statusBar().showMessage(f"失败 · 退出码 {exit_code}")
            tail = self._stderr_tail[-1] if self._stderr_tail else ""
            summary = f"{workflow}运行失败。"
            # 先给用户一句能行动的人话；原始报错缩短保留，完整日志可导出。
            hint = friendly_error_hint(" ".join(self._stderr_tail))
            if hint:
                summary += hint
            elif tail:
                summary += f" 错误信息：{tail[:120]}"
            self.banner.show_message(
                "error",
                summary,
                action_label="导出日志",
                action_callback=self.export_log,
            )

    def build_current_command(self):
        row = self.nav.currentRow()
        if row == 0:
            raise ValueError("项目基线页面是只读预览，暂无可运行工作流。")
        if row == 1:
            return self.build_text_image_command()
        if row == 2:
            return self.build_batch_command()
        if row == 3:
            return self.build_gpt_command()
        return self.build_qr_command()

    def build_text_image_command(self):
        return commands.build_text_image_command(self.text_image_page.form())

    def build_batch_command(self):
        return commands.build_batch_command(self.batch_page.form())

    def build_gpt_command(self):
        return commands.build_gpt_command(self.gpt_page.form())

    def build_qr_command(self):
        return commands.build_qr_command(self.qr_page.form())

    def _clear_preview(self, message: str) -> None:
        self.preview.clear_image()
        self.preview_info_label.setText("")
        self.current_preview_path = None
        self.preview_path_label.setText(message)

    def preview_input(self) -> None:
        path = self.current_input_preview_path()
        if path is None:
            self._clear_preview("没有可预览的输入图片")
            return
        self.load_preview(path)

    def current_input_preview_path(self) -> "Path | None":
        row = self.nav.currentRow()
        if row < 0 or row >= len(self.pages):
            return None
        return self.pages[row].input_preview_path()

    def preview_recent_output(self) -> None:
        if self.last_output_dir is None:
            self._clear_preview("还没有最近输出")
            return
        path = first_output_image(self.last_output_dir)
        if path is None:
            self._clear_preview(f"输出目录暂无可预览图片：{self.last_output_dir}")
            return
        self.load_preview(path)

    def load_preview(self, path: Path) -> None:
        if not path.exists():
            self._clear_preview(f"文件不存在：{path}")
            return
        if not self.preview.load_image(path):
            self._clear_preview(f"无法读取图片：{path}")
            return
        self.current_preview_path = path
        self.preview_path_label.setText(str(path))
        self.preview_info_label.setText(image_info_text(path))

    def handle_dropped_path(self, raw_path: str) -> None:
        path = Path(raw_path)
        if path.is_dir():
            self.batch_page.batch_input.setText(str(path))
            self.nav.setCurrentRow(2)
            self.banner.show_message("info", "目录已填入批量印刷的输入目录。", timeout_ms=4000)
            self.preview_input()
            return
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            self.banner.show_message("warning", f"不支持的文件类型：{path.name}", timeout_ms=4000)
            return
        if self.nav.currentRow() == 3:
            self.gpt_page.gpt_source.setText(str(path))
        elif self.nav.currentRow() == 4:
            self.qr_page.qr_input.setText(str(path))
        else:
            self.batch_page.batch_input.setText(str(path.parent))
            self.batch_page.batch_only.setText(path.name)
            self.nav.setCurrentRow(2)
            self.banner.show_message(
                "info", "图片已填入批量印刷（仅处理该文件）。", timeout_ms=4000
            )
        self.load_preview(path)

    def open_last_output(self) -> None:
        if self.last_output_dir is None:
            self.banner.show_message("info", "还没有运行过工作流。", timeout_ms=4000)
            return
        open_path(self, self.last_output_dir)

    def check_for_updates(self, silent: bool = False) -> None:
        # 主源优先 VPS（app-config 下发的 update_manifest_url，国内可达），
        # 回退 baked 的 GitHub URL；两者任一可达即可检查/下载更新。
        baked = configured_update_manifest_url()
        primary = str(cloud_bootstrap.cached_app_config().get("update_manifest_url", "") or "").strip()
        manifest_url = primary or baked
        fallback_url = baked if primary else ""
        if not manifest_url:
            if not silent:
                QMessageBox.information(
                    self,
                    "未配置更新通道",
                    "尚未配置更新地址：请在“设置 → 云端配置”填写更新地址，"
                    "或设置 DASHDESIGN_UPDATE_MANIFEST_URL。",
                )
            return
        self.statusBar().showMessage("正在检查更新...")
        fetch_update_manifest(manifest_url, self.update_signals, silent, fallback_url=fallback_url)

    def handle_update_result(self, payload: dict, silent: bool) -> None:
        self.statusBar().showMessage("更新检查完成")
        info = evaluate_manifest(payload, APP_VERSION, platform_key())
        if info is None:
            if not silent:
                self._explain_no_update(payload)
            return
        # 只有 Windows 安装版能一键自动更新（Inno 安装器可关旧进程、覆盖
        # Program Files 并重启）。便携版与 macOS 一律降级为跳浏览器手动下载。
        if platform_key() == "windows" and installer.is_installed_build():
            self._offer_windows_auto_update(info)
        else:
            self._offer_manual_download(info)

    def _explain_no_update(self, payload: dict) -> None:
        latest = str(payload.get("version", "")).strip()
        if not latest:
            QMessageBox.warning(self, "更新信息无效", "manifest 中缺少 version。")
        elif version_tuple(latest) <= version_tuple(APP_VERSION):
            QMessageBox.information(self, "已是最新版本", f"当前版本 {APP_VERSION} 已是最新。")
        else:
            QMessageBox.warning(
                self, "更新信息无效", f"版本 {latest} 缺少当前平台安装包 URL。"
            )

    def _update_prompt(self, info: UpdateInfo, tail: str) -> str:
        message = f"发现新版本 {info.version}。\n\n当前版本：{APP_VERSION}"
        if info.notes:
            message += f"\n\n{info.notes}"
        return message + tail

    def _offer_manual_download(self, info: UpdateInfo) -> None:
        message = self._update_prompt(info, "\n\n是否打开安装包下载地址？")
        reply = QMessageBox.question(
            self,
            "发现更新",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(info.url))

    def _offer_windows_auto_update(self, info: UpdateInfo) -> None:
        message = self._update_prompt(
            info, "\n\n是否现在下载并安装？安装程序会关闭 DashDesign 并完成更新。"
        )
        reply = QMessageBox.question(
            self,
            "发现更新",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update_download(info)

    def _start_update_download(self, info: UpdateInfo) -> None:
        self._update_cancelled = False
        dialog = QProgressDialog("正在下载更新…", "取消", 0, info.size or 0, self)
        dialog.setWindowTitle("下载更新")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        if not info.size:
            dialog.setRange(0, 0)  # 未知总量：显示忙碌指示条
        dialog.canceled.connect(self._cancel_update_download)
        self._update_dialog = dialog
        dialog.show()
        download_update(info, self.download_signals, lambda: self._update_cancelled)

    def _cancel_update_download(self) -> None:
        # 只置标志；工作线程会抛 DownloadCancelled 并发 cancelled 信号收尾。
        self._update_cancelled = True
        self.statusBar().showMessage("正在取消更新下载…")

    def _on_update_progress(self, downloaded: int, total: int) -> None:
        dialog = self._update_dialog
        if dialog is None:
            return
        mb = downloaded / (1024 * 1024)
        if total > 0:
            if dialog.maximum() != total:
                dialog.setRange(0, total)
            dialog.setValue(downloaded)
            dialog.setLabelText(f"正在下载更新… {mb:.1f} / {total / (1024 * 1024):.1f} MB")
        else:
            dialog.setLabelText(f"正在下载更新… {mb:.1f} MB")

    def _close_update_dialog(self) -> None:
        if self._update_dialog is not None:
            # 先断开 canceled：QProgressDialog.close() 会自动发 canceled 信号，
            # 若不断开会误触发 _cancel_update_download，把"正常完成"当成用户取消
            # ——这正是此前安装器从未被启动、每次更新都"下载完没反应"的真凶。
            try:
                self._update_dialog.canceled.disconnect(self._cancel_update_download)
            except (TypeError, RuntimeError):
                pass
            self._update_dialog.close()
            self._update_dialog = None

    def _on_update_downloaded(self, path: str) -> None:
        # 在关闭对话框之前取消状态就要读好：关闭动作本身可能改动它。
        was_cancelled = self._update_cancelled
        self._close_update_dialog()
        if was_cancelled:
            # 取消与完成竞态：下载刚好在取消前完成也不要启动安装程序。
            return
        if not installer.launch_windows_installer(Path(path)):
            QMessageBox.warning(
                self,
                "启动安装程序失败",
                f"更新已下载到：\n{path}\n\n请手动运行该安装程序完成更新。",
            )
            return
        # 不主动退出：os.startfile 异步提权，过早 quit 会中止安装器启动（v0.3.2~v0.4.2
        # 的病根）。保持运行，由新安装器的 CloseApplications 在拷贝文件时自动关闭本程序、
        # 装完再重启。用非阻塞 banner 告知，避免模态框挡住安装器的关闭请求。
        self.banner.show_message(
            "info",
            "更新已下载，安装程序正在打开。请在向导中完成安装——"
            "安装时本程序会自动关闭，完成后可重新打开。",
            timeout_ms=0,
        )

    def _on_update_error(self, message: str) -> None:
        self._close_update_dialog()
        QMessageBox.warning(self, "下载更新失败", message)

    def _on_update_cancelled(self) -> None:
        self._close_update_dialog()
        self.statusBar().showMessage("已取消更新下载")

    def handle_update_error(self, message: str, silent: bool) -> None:
        self.statusBar().showMessage("更新检查失败")
        if not silent:
            QMessageBox.warning(self, "更新检查失败", message)

    def open_settings(self) -> None:
        # 外观是实时预览、云端/本机配置由对话框内各自的保存按钮独立提交，
        # 关闭对话框本身不"保存"任何东西，因此不再弹"设置已保存"误导用户。
        SettingsDialog(self).exec()

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于 DashDesign",
            f"DashDesign 印刷图片工作流\n版本 {APP_VERSION}\n\n"
            "面向教育机构海报制作的桌面工具：项目基线、文生图、批量印刷、图片修改与二维码区域清除。",
        )

    def _restore_settings(self) -> None:
        settings = QSettings()
        geometry = settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = settings.value("window/main_splitter")
        if state is not None:
            self.main_splitter.restoreState(state)
            # restoreState 会连旧状态里的可折叠标志一起恢复，须重新禁掉；
            # 旧版本可能已把某侧持久化成 0 宽，一并展开修复。
            self.main_splitter.setChildrenCollapsible(False)
            sizes = self.main_splitter.sizes()
            if len(sizes) == 2 and (sizes[0] == 0 or sizes[1] == 0):
                self.main_splitter.setSizes([620, 420])
        last_output = str(settings.value("window/last_output_dir", ""))
        if last_output and Path(last_output).exists():
            self.last_output_dir = Path(last_output)
        for page in self.pages:
            if hasattr(page, "restore_settings"):
                page.restore_settings(settings)
        row = settings.value("window/nav_row", 0, type=int)
        if 0 <= row < self.nav.count():
            self.nav.setCurrentRow(row)

    def _save_settings(self) -> None:
        settings = QSettings()
        settings.setValue("window/geometry", self.saveGeometry())
        settings.setValue("window/main_splitter", self.main_splitter.saveState())
        settings.setValue("window/nav_row", self.nav.currentRow())
        if self.last_output_dir is not None:
            settings.setValue("window/last_output_dir", str(self.last_output_dir))
        for page in self.pages:
            if hasattr(page, "save_settings"):
                page.save_settings(settings)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.process is not None:
            reply = QMessageBox.question(
                self,
                "任务仍在运行",
                "当前工作流仍在运行，是否停止并退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_process()
        self._save_settings()
        event.accept()


def image_info_text(path: Path) -> str:
    """像素尺寸 / DPI / 文件大小摘要 —— 印刷工具的关键预览信息。"""
    parts: list[str] = []
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = None
        with Image.open(path) as image:
            width, height = image.size
            parts.append(f"{width}×{height} px")
            dpi = image.info.get("dpi")
            if dpi:
                parts.append(f"{round(float(dpi[0]))} DPI")
    except Exception:  # noqa: BLE001 - 预览信息缺失不应影响预览本身
        pass
    try:
        size_mb = path.stat().st_size / (1024 * 1024)
        parts.append(f"{size_mb:.1f} MB" if size_mb >= 0.1 else f"{path.stat().st_size / 1024:.0f} KB")
    except OSError:
        pass
    return " · ".join(parts)


def _install_qt_translations(app: QApplication) -> None:
    """Force Qt's built-in strings (QMessageBox 是/否/确定, edit context menus,
    non-native dialogs) to Chinese so they match the Chinese-only UI, regardless
    of the system locale. Without this Qt shows them in English.

    Only ``qtbase_zh_CN.qm`` matters — it carries all the standard-widget
    strings and is shipped with PySide6. Keep the translator ref on the app so
    it isn't garbage-collected."""
    tr_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translator = QTranslator(app)
    if translator.load("qtbase_zh_CN", tr_dir):
        app.installTranslator(translator)


def create_application(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName("DashDesign")
    app.setOrganizationName("DashDesign")
    _install_qt_translations(app)
    icon_path = app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    # 双平台统一 Fusion 基底：可被 QSS 完整定制，且是 Windows 下唯一
    # 支持暗色 palette 的内置 style；macOS 菜单栏仍走原生。
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    theme.init_theme(app)
    return app
