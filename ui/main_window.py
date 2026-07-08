"""Main window and application factory for the DashDesign desktop client."""

from __future__ import annotations

import html
from collections import deque
from datetime import datetime
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import (
    QElapsedTimer,
    QProcess,
    QProcessEnvironment,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
    qVersion,
)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
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
    configured_update_manifest_url,
    first_output_image,
    platform_key,
    runtime_root,
    version_tuple,
)
from ui import commands, theme
from ui.pages import BaselinePage, BatchPage, GptPage, QrPage, TextImagePage
from ui.updater import UpdateSignals, fetch_update_manifest
from ui.utils import open_path
from ui.widgets import ImagePreview, InfoBanner


class DashDesignQtApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DashDesign 印刷图片工作流")
        self.resize(1180, 780)
        self.setMinimumSize(980, 680)
        self.process: "QProcess | None" = None
        self.last_output_dir: "Path | None" = None
        self.current_preview_path: "Path | None" = None
        self._running = False
        self._stderr_tail: "deque[str]" = deque(maxlen=5)
        self._elapsed = QElapsedTimer()
        self._elapsed_tick = QTimer(self)
        self._elapsed_tick.setInterval(1000)
        self._elapsed_tick.timeout.connect(self._update_elapsed_status)
        self.update_signals = UpdateSignals(self)
        self.update_signals.result.connect(self.handle_update_result)
        self.update_signals.error.connect(self.handle_update_error)

        self._build_actions()
        self._build_menu()
        self._build_ui()
        self._apply_icons()
        if theme.manager() is not None:
            theme.manager().changed.connect(self._on_theme_changed)
        self._restore_settings()
        self.statusBar().showMessage("就绪")
        if configured_update_manifest_url():
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

        self.check_update_action = QAction("检查更新", self)
        self.check_update_action.triggered.connect(lambda: self.check_for_updates(silent=False))

        self.quit_action = QAction("退出", self)
        self.quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.quit_action.triggered.connect(self.close)

    def _build_menu(self) -> None:
        self.menuBar().setNativeMenuBar(True)
        file_menu = self.menuBar().addMenu("文件")
        file_menu.addAction(self.open_project_action)
        file_menu.addAction(self.open_output_action)
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
        for mode, label in (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(mode == current_mode)
            action.triggered.connect(lambda checked=False, m=mode: self._set_theme_mode(m))
            self.theme_action_group.addAction(action)
            appearance_menu.addAction(action)

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
            ("gpt", "GPT 重建"),
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
        title_block.addWidget(self.title_label)
        title_block.addWidget(self.subtitle_label)
        header.addLayout(title_block, 1)
        self.run_button = self._button("运行", self.run_current, primary=True)
        self.stop_button = self._button("停止", self.stop_process)
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

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        self.main_splitter.addWidget(self.stack)
        self.main_splitter.addWidget(self._make_preview_panel())
        self.main_splitter.setSizes([620, 420])

        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(6)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("运行日志"))
        log_header.addStretch(1)
        copy_log = QPushButton("复制")
        copy_log.clicked.connect(self.copy_log)
        export_log = QPushButton("导出…")
        export_log.clicked.connect(self.export_log)
        clear_log = QPushButton("清空")
        log_header.addWidget(copy_log)
        log_header.addWidget(export_log)
        log_header.addWidget(clear_log)
        log_layout.addLayout(log_header)

        self.log = QPlainTextEdit()
        self.log.setObjectName("RunLog")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(80)
        self.log.setMaximumBlockCount(4000)
        log_layout.addWidget(self.log)
        clear_log.clicked.connect(self.log.clear)

        self.v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.v_splitter.addWidget(self.main_splitter)
        self.v_splitter.addWidget(log_container)
        self.v_splitter.setStretchFactor(0, 3)
        self.v_splitter.setStretchFactor(1, 1)
        self.v_splitter.setSizes([520, 200])
        work_layout.addWidget(self.v_splitter, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(140)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

        root_layout.addWidget(work_area, 1)
        self.setCentralWidget(root)
        self.nav.setCurrentRow(0)
        self.append_log("Qt 客户端已启动。请选择工作流并运行。", kind="meta")

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

    def switch_workflow(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        titles = [
            ("项目基线", "查看当前 C 端海报生成基线，后续文生图会自动引用。"),
            ("文生图", "基于当前项目基线生成背景、本地合成海报或完整 Image2 海报。"),
            ("批量印刷", "将根目录或指定目录中的图片输出为印刷规格。"),
            ("GPT 重建", "从源图生成 GPT Image 请求包，必要时直接调用 API。"),
            ("去二维码留空", "只清除指定二维码区域，后期手动添加二维码。"),
        ]
        title, subtitle = titles[row]
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        if row != 4 and self.qr_page.select_button.isChecked():
            self.qr_page.select_button.setChecked(False)
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

    def append_log(self, text: str, kind: str = "out") -> None:
        tokens = theme.current_tokens()
        colors = {
            "out": tokens["log_fg"],
            "err": tokens["log_err"],
            "meta": tokens["log_meta"],
        }
        color = colors.get(kind, tokens["log_fg"])
        timestamp = datetime.now().strftime("%H:%M:%S")
        body = html.escape(text).replace("\n", "<br>")
        self.log.appendHtml(
            f'<span style="color:{tokens["log_meta"]}">[{timestamp}]</span> '
            f'<span style="color:{color}">{body}</span>'
        )
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def copy_log(self) -> None:
        QApplication.clipboard().setText(self.log.toPlainText())
        self.banner.show_message("success", "日志已复制到剪贴板。", timeout_ms=2500)

    def export_log(self) -> None:
        default = str(Path.home() / f"dashdesign-log-{datetime.now():%Y%m%d-%H%M%S}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", default, "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        try:
            Path(path).write_text(self.log.toPlainText(), encoding="utf-8")
        except OSError as exc:
            self.banner.show_message("error", f"日志导出失败：{exc}")
            return
        self.banner.show_message("success", f"日志已导出：{path}", timeout_ms=4000)

    def _update_run_controls(self) -> None:
        can_run = not self._running and self.nav.currentRow() != 0
        self.run_action.setEnabled(can_run)
        self.run_button.setEnabled(can_run)
        self.stop_action.setEnabled(self._running)
        self.stop_button.setEnabled(self._running)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._update_run_controls()
        self.progress.setVisible(running)
        if running:
            self._elapsed.start()
            self._elapsed_tick.start()
            self._update_elapsed_status()
        else:
            self._elapsed_tick.stop()

    def _elapsed_text(self) -> str:
        seconds = self._elapsed.elapsed() // 1000 if self._elapsed.isValid() else 0
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _update_elapsed_status(self) -> None:
        self.statusBar().showMessage(f"运行中 · {self._elapsed_text()}")

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
        self.last_output_dir = output_dir
        self.append_log("$ " + " ".join(command), kind="meta")
        self._set_running(True)

        process_env = QProcessEnvironment.systemEnvironment()
        for key, value in env_updates.items():
            process_env.insert(key, value)

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
        self.append_log("[错误] 工作流进程启动失败：找不到可执行文件或没有执行权限。", kind="err")
        self.statusBar().showMessage("启动失败")
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
        self.append_log("[停止] 已发送 terminate", kind="meta")
        self.process.terminate()
        if not self.process.waitForFinished(2500):
            self.process.kill()

    def read_stdout(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        if data:
            self.append_log(data.rstrip())

    def read_stderr(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardError()).decode(errors="replace")
        if data:
            for line in data.rstrip().splitlines():
                if line.strip():
                    self._stderr_tail.append(line.strip())
            self.append_log(data.rstrip(), kind="err")

    def process_finished(self, exit_code: int, exit_status) -> None:  # type: ignore[no-untyped-def]
        elapsed = self._elapsed_text()
        self.append_log(f"[完成] exit={exit_code} · 用时 {elapsed}", kind="meta")
        self._set_running(False)
        self.process = None
        if exit_code == 0:
            self.statusBar().showMessage(f"完成 · 用时 {elapsed}")
            self.banner.show_message(
                "success",
                f"运行完成，用时 {elapsed}。",
                action_label="打开输出目录",
                action_callback=self.open_last_output,
                timeout_ms=8000,
            )
            self.preview_recent_output()
        else:
            self.statusBar().showMessage(f"失败 · 退出码 {exit_code}")
            tail = self._stderr_tail[-1] if self._stderr_tail else ""
            summary = f"运行失败（退出码 {exit_code}）。"
            if tail:
                summary += f" 最后错误：{tail[:160]}"
            self.banner.show_message(
                "error",
                summary,
                action_label="查看日志",
                action_callback=self._scroll_log_to_end,
            )

    def _scroll_log_to_end(self) -> None:
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

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

    def preview_input(self) -> None:
        path = self.current_input_preview_path()
        if path is None:
            self.preview_path_label.setText("没有可预览的输入图片")
            self.preview_info_label.setText("")
            self.preview.clear_image()
            self.current_preview_path = None
            return
        self.load_preview(path)

    def current_input_preview_path(self) -> "Path | None":
        row = self.nav.currentRow()
        if row < 0 or row >= len(self.pages):
            return None
        return self.pages[row].input_preview_path()

    def preview_recent_output(self) -> None:
        if self.last_output_dir is None:
            self.preview_path_label.setText("还没有最近输出")
            return
        path = first_output_image(self.last_output_dir)
        if path is None:
            self.preview_path_label.setText(f"输出目录暂无可预览图片：{self.last_output_dir}")
            return
        self.load_preview(path)

    def load_preview(self, path: Path) -> None:
        if not path.exists():
            self.preview_path_label.setText(f"文件不存在：{path}")
            return
        if not self.preview.load_image(path):
            self.preview_path_label.setText(f"无法读取图片：{path}")
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
        manifest_url = configured_update_manifest_url()
        if not manifest_url:
            if not silent:
                QMessageBox.information(
                    self,
                    "未配置更新通道",
                    "请先配置 DASHDESIGN_UPDATE_MANIFEST_URL 指向 release manifest。",
                )
            return
        self.statusBar().showMessage("正在检查更新...")
        fetch_update_manifest(manifest_url, self.update_signals, silent)

    def handle_update_result(self, payload: dict, silent: bool) -> None:
        self.statusBar().showMessage("更新检查完成")
        latest_version = str(payload.get("version", "")).strip()
        if not latest_version:
            if not silent:
                QMessageBox.warning(self, "更新信息无效", "manifest 中缺少 version。")
            return
        if version_tuple(latest_version) <= version_tuple(APP_VERSION):
            if not silent:
                QMessageBox.information(self, "已是最新版本", f"当前版本 {APP_VERSION} 已是最新。")
            return

        platforms = payload.get("platforms", {})
        platform_info = platforms.get(platform_key(), {}) if isinstance(platforms, dict) else {}
        download_url = platform_info.get("url") or payload.get("url")
        if not download_url:
            if not silent:
                QMessageBox.warning(self, "更新信息无效", f"版本 {latest_version} 缺少当前平台安装包 URL。")
            return

        notes = payload.get("notes", "")
        message = f"发现新版本 {latest_version}。\n\n当前版本：{APP_VERSION}"
        if notes:
            message += f"\n\n{notes}"
        message += "\n\n是否打开安装包下载地址？"
        reply = QMessageBox.question(
            self,
            "发现更新",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(str(download_url)))

    def handle_update_error(self, message: str, silent: bool) -> None:
        self.statusBar().showMessage("更新检查失败")
        if not silent:
            QMessageBox.warning(self, "更新检查失败", message)

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于 DashDesign",
            f"DashDesign 印刷图片工作流\n版本 {APP_VERSION}\n\n"
            "PySide6/Qt 客户端原型，用于基线文生图、批量印刷输出、GPT 重建请求包和二维码区域清除。",
        )

    def _restore_settings(self) -> None:
        settings = QSettings()
        geometry = settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        for splitter, key in ((self.main_splitter, "window/main_splitter"), (self.v_splitter, "window/v_splitter")):
            state = settings.value(key)
            if state is not None:
                splitter.restoreState(state)
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
        settings.setValue("window/v_splitter", self.v_splitter.saveState())
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


def create_application(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName("DashDesign")
    app.setOrganizationName("DashDesign")
    # 双平台统一 Fusion 基底：可被 QSS 完整定制，且是 Windows 下唯一
    # 支持暗色 palette 的内置 style；macOS 菜单栏仍走原生。
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    theme.init_theme(app)
    return app
