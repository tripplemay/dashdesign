"""Main window and application factory for the DashDesign desktop client."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QSize, Qt, QTimer, QUrl, qVersion
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
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
from ui import commands
from ui.pages import BaselinePage, BatchPage, GptPage, QrPage, TextImagePage
from ui.theme import app_stylesheet
from ui.updater import UpdateSignals, fetch_update_manifest
from ui.utils import open_path
from ui.widgets import ImagePreview


class DashDesignQtApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DashDesign 印刷图片工作流")
        self.resize(1180, 780)
        self.setMinimumSize(980, 680)
        self.process: "QProcess | None" = None
        self.last_output_dir: "Path | None" = None
        self.current_preview_path: "Path | None" = None
        self.update_signals = UpdateSignals(self)
        self.update_signals.result.connect(self.handle_update_result)
        self.update_signals.error.connect(self.handle_update_error)

        self._build_actions()
        self._build_menu()
        self._build_ui()
        self.setStyleSheet(app_stylesheet())
        self.statusBar().showMessage(f"Qt {qVersion()} · 就绪")
        if configured_update_manifest_url():
            QTimer.singleShot(1600, lambda: self.check_for_updates(silent=True))

    def _build_actions(self) -> None:
        self.run_action = QAction("运行当前工作流", self)
        self.run_action.setShortcut("Meta+R")
        self.run_action.triggered.connect(self.run_current)

        self.stop_action = QAction("停止", self)
        self.stop_action.setShortcut("Meta+.")
        self.stop_action.triggered.connect(self.stop_process)
        self.stop_action.setEnabled(False)

        self.open_project_action = QAction("打开项目目录", self)
        self.open_project_action.triggered.connect(lambda: open_path(self, PROJECT_ROOT))

        self.open_output_action = QAction("打开最近输出", self)
        self.open_output_action.triggered.connect(self.open_last_output)

        self.check_update_action = QAction("检查更新", self)
        self.check_update_action.triggered.connect(lambda: self.check_for_updates(silent=False))

        self.quit_action = QAction("退出", self)
        self.quit_action.setShortcut("Meta+Q")
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
        self.nav.setFixedWidth(196)
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setSpacing(2)
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
        header.addWidget(self._button("运行", self.run_current, primary=True))
        header.addWidget(self._button("停止", self.stop_process))
        header.addWidget(self._button("打开输出", self.open_last_output))
        work_layout.addLayout(header)

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

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.stack = QStackedWidget()
        for page in self.pages:
            self.stack.addWidget(page)
        main_splitter.addWidget(self.stack)
        main_splitter.addWidget(self._make_preview_panel())
        main_splitter.setSizes([620, 420])
        work_layout.addWidget(main_splitter, 3)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("运行日志"))
        log_header.addStretch(1)
        clear_log = QPushButton("清空日志")
        log_header.addWidget(clear_log)
        work_layout.addLayout(log_header)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        self.log.setMaximumBlockCount(4000)
        work_layout.addWidget(self.log, 2)
        clear_log.clicked.connect(self.log.clear)

        root_layout.addWidget(work_area, 1)
        self.setCentralWidget(root)
        self.nav.setCurrentRow(0)
        self.append_log("Qt 客户端已启动。请选择工作流并运行。")

    def _button(self, label: str, slot, primary: bool = False) -> QPushButton:  # type: ignore[no-untyped-def]
        button = QPushButton(label)
        button.clicked.connect(slot)
        if primary:
            button.setObjectName("PrimaryButton")
        return button

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

        self.preview = ImagePreview()
        self.preview.pathDropped.connect(self.handle_dropped_path)
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
        self.preview_input()

    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def run_current(self) -> None:
        if self.process is not None:
            QMessageBox.warning(self, "正在运行", "已有工作流正在运行，请先停止或等待完成。")
            return
        try:
            command, output_dir, env_updates = self.build_current_command()
        except ValueError as exc:
            QMessageBox.critical(self, "参数错误", str(exc))
            return

        self.last_output_dir = output_dir
        self.append_log("$ " + " ".join(command))
        self.statusBar().showMessage("正在运行...")
        self.run_action.setEnabled(False)
        self.stop_action.setEnabled(True)

        process_env = QProcessEnvironment.systemEnvironment()
        for key, value in env_updates.items():
            process_env.insert(key, value)

        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(runtime_root()))
        self.process.setProcessEnvironment(process_env)
        self.process.readyReadStandardOutput.connect(self.read_stdout)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.process_finished)
        self.process.start(command[0], command[1:])

    def stop_process(self) -> None:
        if self.process is None:
            return
        self.append_log("[停止] 已发送 terminate")
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
            self.append_log(data.rstrip())

    def process_finished(self, exit_code: int, exit_status) -> None:  # type: ignore[no-untyped-def]
        self.append_log(f"\n[完成] exit={exit_code}")
        self.statusBar().showMessage("完成" if exit_code == 0 else f"失败 exit={exit_code}")
        self.run_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.process = None
        if exit_code == 0:
            self.preview_recent_output()

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

    def handle_dropped_path(self, raw_path: str) -> None:
        path = Path(raw_path)
        if path.is_dir():
            self.batch_page.batch_input.setText(str(path))
            self.nav.setCurrentRow(2)
            self.preview_input()
            return
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        if self.nav.currentRow() == 3:
            self.gpt_page.gpt_source.setText(str(path))
        elif self.nav.currentRow() == 4:
            self.qr_page.qr_input.setText(str(path))
        else:
            self.batch_page.batch_input.setText(str(path.parent))
            self.batch_page.batch_only.setText(path.name)
            self.nav.setCurrentRow(2)
        self.load_preview(path)

    def open_last_output(self) -> None:
        if self.last_output_dir is None:
            QMessageBox.information(self, "无输出", "还没有运行过工作流。")
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
        event.accept()


def create_application(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName("DashDesign")
    app.setOrganizationName("DashDesign")
    if "macOS" in QStyleFactory.keys():
        app.setStyle("macOS")
    return app
