#!/usr/bin/env python3
"""PySide6 desktop client for DashDesign print workflows."""

from __future__ import annotations

import os
import json
import runpy
import sys
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QSize, Qt, QTimer, QUrl, Signal, qVersion
from PySide6.QtGui import QAction, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)


PROJECT_ROOT = Path(__file__).resolve().parent
APP_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve()
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


def read_app_version() -> str:
    for candidate in (APP_ROOT / "VERSION", PROJECT_ROOT / "VERSION"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value.lstrip("v")
    return os.environ.get("DASHDESIGN_VERSION", "0.1.0").lstrip("v")


def configured_update_manifest_url() -> str:
    env_url = os.environ.get("DASHDESIGN_UPDATE_MANIFEST_URL", "").strip()
    if env_url:
        return env_url
    for candidate in (APP_ROOT / "UPDATE_MANIFEST_URL", PROJECT_ROOT / "UPDATE_MANIFEST_URL"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


APP_VERSION = read_app_version()


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def worker_prefix() -> list[str]:
    if is_packaged():
        return [str(Path(sys.executable).resolve()), "--worker"]
    return [PYTHON, str(PROJECT_ROOT / "desktop_qt_app.py"), "--worker"]


def runtime_root() -> Path:
    if is_packaged():
        return APP_ROOT
    return PROJECT_ROOT


def runtime_tool_path() -> Path:
    name = "realesrgan-ncnn-vulkan.exe" if sys.platform.startswith("win") else "realesrgan-ncnn-vulkan"
    return runtime_root() / "tools" / name


def runtime_model_dir() -> Path:
    return runtime_root() / "tools" / "models"


class PathField(QWidget):
    def __init__(
        self,
        label: str,
        value: str = "",
        mode: str = "file",
        placeholder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode
        self.label = QLabel(label)
        self.label.setMinimumWidth(92)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        self.button = QPushButton("选择")
        self.button.clicked.connect(self.choose)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.label)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:
        self.edit.setText(value)

    def choose(self) -> None:
        initial = self.text() or str(PROJECT_ROOT)
        if self.mode == "dir":
            selected = QFileDialog.getExistingDirectory(self, "选择目录", initial)
        else:
            selected, _ = QFileDialog.getOpenFileName(
                self,
                "选择图片",
                str(PROJECT_ROOT),
                "Images (*.jpg *.jpeg *.png *.webp *.tif *.tiff *.bmp);;All Files (*)",
            )
        if selected:
            self.setText(selected)


class ImagePreview(QGraphicsView):
    pathDropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_item: QGraphicsPixmapItem | None = None
        self.zoom_level = 1.0
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHints(self.renderHints())
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumSize(QSize(360, 300))

    def load_image(self, path: Path) -> bool:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return False
        self.scene.clear()
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(self.pixmap_item)
        self.scene.setSceneRect(self.pixmap_item.boundingRect())
        self.zoom_level = 1.0
        QTimer.singleShot(0, self.fit_image)
        return True

    def fit_image(self) -> None:
        if self.pixmap_item is None:
            return
        self.fitInView(self.pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoom_level = 1.0

    def actual_size(self) -> None:
        if self.pixmap_item is None:
            return
        self.resetTransform()
        self.zoom_level = 1.0

    def zoom(self, factor: float) -> None:
        if self.pixmap_item is None:
            return
        self.zoom_level *= factor
        self.scale(factor, factor)

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.pixmap_item is None:
            super().wheelEvent(event)
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom(factor)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self.pathDropped.emit(path)
            event.acceptProposedAction()


class UpdateSignals(QObject):
    result = Signal(dict, bool)
    error = Signal(str, bool)


def version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.strip().lstrip("v").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits or "0"))
    return tuple(parts)


def platform_key() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


class DashDesignQtApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DashDesign 印刷图片工作流")
        self.resize(1180, 780)
        self.setMinimumSize(980, 680)
        self.process: QProcess | None = None
        self.last_output_dir: Path | None = None
        self.current_preview_path: Path | None = None
        self.update_signals = UpdateSignals(self)
        self.update_signals.result.connect(self.handle_update_result)
        self.update_signals.error.connect(self.handle_update_error)

        self._build_actions()
        self._build_menu()
        self._build_ui()
        self._apply_style()
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
        self.open_project_action.triggered.connect(lambda: self.open_path(PROJECT_ROOT))

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

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._make_batch_page())
        self.stack.addWidget(self._make_gpt_page())
        self.stack.addWidget(self._make_qr_page())
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

    def _make_batch_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        paths = QGroupBox("输入输出")
        path_layout = QVBoxLayout(paths)
        self.batch_input = PathField("输入目录", str(PROJECT_ROOT), "dir")
        self.batch_output = PathField("输出目录", str(PROJECT_ROOT / "print_ready_desktop_qt"), "dir")
        path_layout.addWidget(self.batch_input)
        path_layout.addWidget(self.batch_output)
        layout.addWidget(paths)

        workflow = QGroupBox("处理方式")
        workflow_layout = QVBoxLayout(workflow)
        self.workflow_group = QButtonGroup(self)
        self.style_radio = QRadioButton("保留原字效高清化（Real-ESRGAN，推荐）")
        self.pil_radio = QRadioButton("基础 200dpi 输出（PIL/Lanczos，稳定兜底）")
        self.style_radio.setChecked(True)
        self.workflow_group.addButton(self.style_radio)
        self.workflow_group.addButton(self.pil_radio)
        workflow_layout.addWidget(self.style_radio)
        workflow_layout.addWidget(self.pil_radio)
        layout.addWidget(workflow)

        options = QGroupBox("参数")
        option_layout = QGridLayout(options)
        self.batch_dpi = QLineEdit("200")
        self.batch_only = QLineEdit()
        self.batch_only.setPlaceholderText("可选：只处理某个文件名")
        self.batch_force = QCheckBox("覆盖/强制重新生成")
        self.batch_force.setChecked(True)
        self.batch_keep_masters = QCheckBox("保留 Real-ESRGAN 中间 master")
        option_layout.addWidget(QLabel("DPI"), 0, 0)
        option_layout.addWidget(self.batch_dpi, 0, 1)
        option_layout.addWidget(QLabel("只处理文件"), 1, 0)
        option_layout.addWidget(self.batch_only, 1, 1)
        option_layout.addWidget(self.batch_force, 2, 1)
        option_layout.addWidget(self.batch_keep_masters, 3, 1)
        option_layout.setColumnStretch(1, 1)
        layout.addWidget(options)
        layout.addStretch(1)
        return page

    def _make_gpt_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        paths = QGroupBox("源图与输出")
        path_layout = QVBoxLayout(paths)
        self.gpt_source = PathField("源图片", "", "file")
        self.gpt_output = PathField(
            "输出目录",
            str(PROJECT_ROOT / "workflow_samples" / "desktop_gpt_image_rebuild_qt"),
            "dir",
        )
        path_layout.addWidget(self.gpt_source)
        path_layout.addWidget(self.gpt_output)
        layout.addWidget(paths)

        api = QGroupBox("生成设置")
        api_layout = QGridLayout(api)
        self.gpt_mode = QComboBox()
        self.gpt_mode.addItems(["edit", "generate"])
        self.gpt_dpi = QLineEdit("200")
        self.gpt_execute = QCheckBox("立即调用 API")
        self.gpt_base_url = QLineEdit()
        self.gpt_base_url.setPlaceholderText("可选：OpenAI-compatible base URL")
        self.gpt_api_key = QLineEdit()
        self.gpt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.gpt_api_key.setPlaceholderText("可选：仅本次进程使用，不写入文件")
        self.gpt_description = QLineEdit()
        self.gpt_description.setPlaceholderText("可选：补充设计描述或约束")
        api_layout.addWidget(QLabel("模式"), 0, 0)
        api_layout.addWidget(self.gpt_mode, 0, 1)
        api_layout.addWidget(QLabel("DPI"), 1, 0)
        api_layout.addWidget(self.gpt_dpi, 1, 1)
        api_layout.addWidget(self.gpt_execute, 2, 1)
        api_layout.addWidget(QLabel("Base URL"), 3, 0)
        api_layout.addWidget(self.gpt_base_url, 3, 1)
        api_layout.addWidget(QLabel("API Key"), 4, 0)
        api_layout.addWidget(self.gpt_api_key, 4, 1)
        api_layout.addWidget(QLabel("描述补充"), 5, 0)
        api_layout.addWidget(self.gpt_description, 5, 1)
        api_layout.setColumnStretch(1, 1)
        layout.addWidget(api)
        layout.addStretch(1)
        return page

    def _make_qr_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        paths = QGroupBox("输入输出")
        path_layout = QVBoxLayout(paths)
        self.qr_input = PathField("输入图片", "", "file")
        self.qr_output = PathField("输出目录", str(PROJECT_ROOT / "single_no_qr_desktop_qt"), "dir")
        path_layout.addWidget(self.qr_input)
        path_layout.addWidget(self.qr_output)
        layout.addWidget(paths)

        params = QGroupBox("清除区域")
        params_layout = QGridLayout(params)
        self.qr_box = QLineEdit()
        self.qr_box.setPlaceholderText("x1,y1,x2,y2")
        self.qr_reference_size = QLineEdit()
        self.qr_reference_size.setPlaceholderText("可选：如 3238x1295")
        self.qr_margin = QLineEdit("0.55")
        self.qr_radius = QLineEdit("21")
        params_layout.addWidget(QLabel("区域"), 0, 0)
        params_layout.addWidget(self.qr_box, 0, 1)
        params_layout.addWidget(QLabel("参考尺寸"), 1, 0)
        params_layout.addWidget(self.qr_reference_size, 1, 1)
        params_layout.addWidget(QLabel("边界比例"), 2, 0)
        params_layout.addWidget(self.qr_margin, 2, 1)
        params_layout.addWidget(QLabel("修补半径"), 3, 0)
        params_layout.addWidget(self.qr_radius, 3, 1)
        params_layout.setColumnStretch(1, 1)
        layout.addWidget(params)
        layout.addStretch(1)
        return page

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

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                font-size: 13px;
            }
            QListWidget {
                background: #f4f4f4;
                border-right: 1px solid #d8d8d8;
                padding: 10px 8px;
            }
            QListWidget::item {
                border-radius: 7px;
                padding: 8px 10px;
            }
            QListWidget::item:selected {
                background: #222222;
                color: #ffffff;
            }
            QLabel#Title {
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#Subtitle {
                color: #666666;
            }
            QGroupBox {
                border: 1px solid #d9d9d9;
                border-radius: 8px;
                margin-top: 12px;
                padding: 12px;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #333333;
                font-weight: 600;
            }
            QPushButton {
                min-height: 24px;
                padding: 4px 12px;
                border-radius: 6px;
            }
            QPushButton#PrimaryButton {
                background: #1f1f1f;
                color: #ffffff;
            }
            QRadioButton, QCheckBox {
                color: #222222;
                min-height: 24px;
                spacing: 8px;
            }
            QRadioButton::indicator, QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border: 1px solid #777777;
                background: #ffffff;
            }
            QRadioButton::indicator {
                border-radius: 8px;
            }
            QRadioButton::indicator:checked {
                background: #222222;
                border: 1px solid #222222;
            }
            QCheckBox::indicator {
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background: #222222;
                border: 2px solid #222222;
            }
            QLineEdit, QComboBox {
                min-height: 26px;
            }
            QPlainTextEdit {
                background: #111111;
                color: #eeeeee;
                border-radius: 8px;
                padding: 8px;
                font-family: Menlo, Monaco, Consolas, monospace;
                font-size: 12px;
            }
            QWidget#PreviewPanel {
                background: #ffffff;
                border: 1px solid #d9d9d9;
                border-radius: 8px;
            }
            """
        )

    def switch_workflow(self, row: int) -> None:
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        titles = [
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

    def build_current_command(self) -> tuple[list[str], Path, dict[str, str]]:
        row = self.nav.currentRow()
        if row == 0:
            return self.build_batch_command()
        if row == 1:
            return self.build_gpt_command()
        return self.build_qr_command()

    def build_batch_command(self) -> tuple[list[str], Path, dict[str, str]]:
        input_dir = Path(self.batch_input.text()).expanduser()
        output_dir = Path(self.batch_output.text()).expanduser()
        if not input_dir.exists():
            raise ValueError("输入目录不存在")
        dpi = self.batch_dpi.text().strip()
        if not dpi:
            raise ValueError("请填写 DPI")
        command = worker_prefix()
        if self.style_radio.isChecked():
            command += [
                "batch-style",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
                "--dpi",
                dpi,
                "--realesrgan-binary",
                str(runtime_tool_path()),
                "--realesrgan-model-dir",
                str(runtime_model_dir()),
            ]
            if self.batch_force.isChecked():
                command.append("--force")
            if self.batch_keep_masters.isChecked():
                command.append("--keep-masters")
        else:
            command += [
                "batch-pil",
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
                "--dpi",
                dpi,
            ]
        only = self.batch_only.text().strip()
        if only:
            command += ["--only", only]
        return command, output_dir, {}

    def build_gpt_command(self) -> tuple[list[str], Path, dict[str, str]]:
        source = Path(self.gpt_source.text()).expanduser()
        output_dir = Path(self.gpt_output.text()).expanduser()
        if not source.exists():
            raise ValueError("源图片不存在")
        command = [
            *worker_prefix(),
            "gpt",
            str(source),
            "--output-dir",
            str(output_dir),
            "--print-dpi",
            self.gpt_dpi.text().strip(),
            "--api-mode",
            self.gpt_mode.currentText(),
        ]
        description = self.gpt_description.text().strip()
        if description:
            command += ["--description", description]
        if self.gpt_execute.isChecked():
            command.append("--execute")

        env: dict[str, str] = {}
        if self.gpt_base_url.text().strip():
            env["OPENAI_BASE_URL"] = self.gpt_base_url.text().strip()
        if self.gpt_api_key.text().strip():
            env["OPENAI_API_KEY"] = self.gpt_api_key.text().strip()
        return command, output_dir, env

    def build_qr_command(self) -> tuple[list[str], Path, dict[str, str]]:
        source = Path(self.qr_input.text()).expanduser()
        output_dir = Path(self.qr_output.text()).expanduser()
        if not source.exists():
            raise ValueError("输入图片不存在")
        box = self.qr_box.text().strip()
        if not box:
            raise ValueError("请填写清除区域 x1,y1,x2,y2")
        command = [
            *worker_prefix(),
            "qr",
            str(source),
            "--output-dir",
            str(output_dir),
            "--box",
            box,
            "--margin-ratio",
            self.qr_margin.text().strip(),
            "--inpaint-radius",
            self.qr_radius.text().strip(),
        ]
        reference_size = self.qr_reference_size.text().strip()
        if reference_size:
            command += ["--reference-size", reference_size]
        return command, output_dir, {}

    def preview_input(self) -> None:
        path = self.current_input_preview_path()
        if path is None:
            self.preview_path_label.setText("没有可预览的输入图片")
            return
        self.load_preview(path)

    def current_input_preview_path(self) -> Path | None:
        row = self.nav.currentRow()
        if row == 0:
            input_dir = Path(self.batch_input.text()).expanduser()
            only = self.batch_only.text().strip()
            if only:
                path = input_dir / only
                return path if path.exists() else None
            return first_image(input_dir)
        if row == 1:
            path = Path(self.gpt_source.text()).expanduser()
            return path if path.exists() else None
        path = Path(self.qr_input.text()).expanduser()
        return path if path.exists() else None

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
            self.batch_input.setText(str(path))
            self.nav.setCurrentRow(0)
            self.preview_input()
            return
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            return
        if self.nav.currentRow() == 1:
            self.gpt_source.setText(str(path))
        elif self.nav.currentRow() == 2:
            self.qr_input.setText(str(path))
        else:
            self.batch_input.setText(str(path.parent))
            self.batch_only.setText(path.name)
        self.load_preview(path)

    def open_last_output(self) -> None:
        if self.last_output_dir is None:
            QMessageBox.information(self, "无输出", "还没有运行过工作流。")
            return
        self.open_path(self.last_output_dir)

    def open_path(self, path: Path) -> None:
        path = path.expanduser()
        if not path.exists():
            QMessageBox.warning(self, "路径不存在", str(path))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

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

        def worker() -> None:
            try:
                request = urllib.request.Request(
                    manifest_url,
                    headers={"User-Agent": f"DashDesign/{APP_VERSION}"},
                )
                with urllib.request.urlopen(request, timeout=12) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.update_signals.result.emit(payload, silent)
            except Exception as exc:  # noqa: BLE001
                self.update_signals.error.emit(str(exc), silent)

        threading.Thread(target=worker, daemon=True).start()

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
            "PySide6/Qt 客户端原型，用于批量印刷输出、GPT 重建请求包和二维码区域清除。",
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


def first_image(directory: Path) -> Path | None:
    if not directory.exists() or not directory.is_dir():
        return None
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            return path
    return None


def first_output_image(directory: Path) -> Path | None:
    review = directory / "review" / "contact_sheet.jpg"
    if review.exists():
        return review
    direct = first_image(directory)
    if direct is not None:
        return direct
    review_dir = directory / "review"
    return first_image(review_dir)


def run_script_worker(script_name: str, args: list[str]) -> int:
    script_map = {
        "batch-style": runtime_root() / "scripts" / "batch_style_preserved_print.py",
        "batch-pil": runtime_root() / "scripts" / "prepare_print_assets.py",
        "gpt": runtime_root() / "scripts" / "gpt_image_rebuild.py",
        "qr": runtime_root() / "scripts" / "remove_qr_area.py",
    }
    script_path = script_map.get(script_name)
    if script_path is None:
        print(f"Unknown worker: {script_name}", file=sys.stderr)
        return 2
    if not script_path.exists():
        print(f"Worker script not found: {script_path}", file=sys.stderr)
        return 2

    old_argv = sys.argv[:]
    old_path = sys.path[:]
    sys.argv = [str(script_path), *args]
    sys.path.insert(0, str(script_path.parent))
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    finally:
        sys.argv = old_argv
        sys.path = old_path
    return 0


def create_application(argv: list[str]) -> QApplication:
    app = QApplication(argv)
    app.setApplicationName("DashDesign")
    app.setOrganizationName("DashDesign")
    if "macOS" in QStyleFactory.keys():
        app.setStyle("macOS")
    return app


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        return run_script_worker(sys.argv[2], sys.argv[3:])
    app = create_application(sys.argv)
    window = DashDesignQtApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
