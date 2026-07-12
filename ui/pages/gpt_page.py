"""Image editing page: edit an existing poster image with AI (single mode)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDoubleSpinBox,
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
from ui.print_size import suggest_print_size_cm
from ui.utils import scrollable_page_layout
from ui.widgets import PathField


class GptPage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
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
        layout.addWidget(paths)

        settings_group = QGroupBox("修改设置")
        settings_layout = QGridLayout(settings_group)
        settings_layout.addWidget(QLabel("修改要求"), 0, 0)
        self.gpt_description = QPlainTextEdit()
        self.gpt_description.setObjectName("TextPrompt")
        self.gpt_description.setPlaceholderText(
            "用一句话说明要怎么改，例如：把背景换成蓝天草地、去掉左下角的文字。"
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
        layout.addStretch(1)

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
            source=self.gpt_source.text(),
            output_dir=self.gpt_output.text(),
            width_cm=str(self.gpt_width_cm.value()),
            height_cm=str(self.gpt_height_cm.value()),
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