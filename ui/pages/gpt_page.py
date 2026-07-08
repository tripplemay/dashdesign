"""GPT image rebuild page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app_runtime import PROJECT_ROOT
from ui.commands import GptForm
from ui.widgets import PathField


class GptPage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
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

    def form(self) -> GptForm:
        return GptForm(
            source=self.gpt_source.text(),
            output_dir=self.gpt_output.text(),
            mode=self.gpt_mode.currentText(),
            dpi=self.gpt_dpi.text(),
            description=self.gpt_description.text(),
            execute=self.gpt_execute.isChecked(),
            base_url=self.gpt_base_url.text(),
            api_key=self.gpt_api_key.text(),
        )

    def input_preview_path(self) -> "Path | None":
        if not self.gpt_source.text():
            return None
        path = Path(self.gpt_source.text()).expanduser()
        return path if path.exists() else None

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/gpt/output_dir", self.gpt_output.text())
        settings.setValue("pages/gpt/mode", self.gpt_mode.currentText())
        settings.setValue("pages/gpt/dpi", self.gpt_dpi.text())
        settings.setValue("pages/gpt/base_url", self.gpt_base_url.text())

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.gpt_output.setText(str(settings.value("pages/gpt/output_dir", self.gpt_output.text())))
        mode = settings.value("pages/gpt/mode")
        if mode is not None:
            index = self.gpt_mode.findText(str(mode))
            if index >= 0:
                self.gpt_mode.setCurrentIndex(index)
        self.gpt_dpi.setText(str(settings.value("pages/gpt/dpi", self.gpt_dpi.text())))
        self.gpt_base_url.setText(str(settings.value("pages/gpt/base_url", self.gpt_base_url.text())))
