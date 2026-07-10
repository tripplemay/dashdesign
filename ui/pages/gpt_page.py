"""GPT image rebuild page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.output_paths import default_output, restore_output
from ui import api_config
from ui.commands import GptForm
from ui.utils import scrollable_page_layout
from ui.widgets import PathField

_MODE_HINTS = {
    "edit": "以源图为底做修改（/images/edits）：保留原构图，适合局部重绘。",
    "generate": "源图仅作参考、完全重新生成（/images/generations）：构图可能大改。",
}


class GptPage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = scrollable_page_layout(self)

        paths = QGroupBox("源图与输出")
        path_layout = QVBoxLayout(paths)
        self.gpt_source = PathField("源图片", "", "file", placeholder="拖入或选择需要重建的海报图")
        self.gpt_output = PathField(
            "输出目录",
            default_output("workflow_samples", "desktop_gpt_image_rebuild_qt"),
            "dir",
        )
        path_layout.addWidget(self.gpt_source)
        path_layout.addWidget(self.gpt_output)
        layout.addWidget(paths)

        settings_group = QGroupBox("生成设置")
        settings_layout = QGridLayout(settings_group)
        mode_row = QHBoxLayout()
        self.gpt_mode = QComboBox()
        self.gpt_mode.addItem("编辑原图", "edit")
        self.gpt_mode.addItem("参考重生成", "generate")
        self.gpt_mode.currentIndexChanged.connect(self._sync_mode_hint)
        mode_row.addWidget(self.gpt_mode)
        self.gpt_dpi = QSpinBox()
        self.gpt_dpi.setRange(30, 600)
        self.gpt_dpi.setValue(200)
        self.gpt_dpi.setToolTip("印刷输出分辨率：写真/展架常用 200，大幅喷绘可用 150。")
        mode_row.addSpacing(12)
        mode_row.addWidget(QLabel("DPI"))
        mode_row.addWidget(self.gpt_dpi)
        mode_row.addStretch(1)
        settings_layout.addWidget(QLabel("模式"), 0, 0)
        settings_layout.addLayout(mode_row, 0, 1)
        self.mode_hint = QLabel(_MODE_HINTS["edit"])
        self.mode_hint.setObjectName("Subtitle")
        self.mode_hint.setWordWrap(True)
        settings_layout.addWidget(self.mode_hint, 1, 1)
        settings_layout.addWidget(QLabel("描述补充"), 2, 0)
        self.gpt_description = QPlainTextEdit()
        self.gpt_description.setObjectName("TextPrompt")
        self.gpt_description.setPlaceholderText("可选：补充设计描述或约束（多行）")
        self.gpt_description.setMaximumHeight(88)
        settings_layout.addWidget(self.gpt_description, 2, 1)
        settings_layout.setColumnStretch(1, 1)
        layout.addWidget(settings_group)
        layout.addStretch(1)

    def _sync_mode_hint(self) -> None:
        mode = str(self.gpt_mode.currentData() or "edit")
        self.mode_hint.setText(_MODE_HINTS.get(mode, ""))

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
            source=self.gpt_source.text(),
            output_dir=self.gpt_output.text(),
            mode=str(self.gpt_mode.currentData() or "edit"),
            dpi=str(self.gpt_dpi.value()),
            description=self.gpt_description.toPlainText(),
            base_url=api_config.load_base_url(),
            api_key=api_config.load_api_key(),
        )

    def input_preview_path(self) -> "Path | None":
        if not self.gpt_source.text():
            return None
        path = Path(self.gpt_source.text()).expanduser()
        return path if path.exists() else None

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/gpt/output_dir", self.gpt_output.text())
        settings.setValue("pages/gpt/mode", str(self.gpt_mode.currentData()))
        settings.setValue("pages/gpt/dpi", self.gpt_dpi.value())

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.gpt_output.setText(
            restore_output(
                str(settings.value("pages/gpt/output_dir", "")),
                "workflow_samples",
                "desktop_gpt_image_rebuild_qt",
            )
        )
        mode = settings.value("pages/gpt/mode")
        if mode is not None:
            index = self.gpt_mode.findData(str(mode))
            if index >= 0:
                self.gpt_mode.setCurrentIndex(index)
        self.gpt_dpi.setValue(settings.value("pages/gpt/dpi", self.gpt_dpi.value(), type=int))