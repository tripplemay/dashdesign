"""Batch print output page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app_runtime import PROJECT_ROOT, first_image
from ui.commands import BatchForm
from ui.widgets import PathField


class BatchPage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        paths = QGroupBox("输入输出")
        path_layout = QVBoxLayout(paths)
        self.batch_input = PathField(
            "输入目录",
            "",
            "dir",
            placeholder="选择包含源图片的目录（文件名需含尺寸，如 200乘以80）",
        )
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

    def form(self) -> BatchForm:
        return BatchForm(
            input_dir=self.batch_input.text(),
            output_dir=self.batch_output.text(),
            style_mode=self.style_radio.isChecked(),
            dpi=self.batch_dpi.text(),
            only=self.batch_only.text(),
            force=self.batch_force.isChecked(),
            keep_masters=self.batch_keep_masters.isChecked(),
        )

    def input_preview_path(self) -> "Path | None":
        if not self.batch_input.text():
            return None
        input_dir = Path(self.batch_input.text()).expanduser()
        only = self.batch_only.text().strip()
        if only:
            path = input_dir / only
            return path if path.exists() else None
        return first_image(input_dir)

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/batch/input_dir", self.batch_input.text())
        settings.setValue("pages/batch/output_dir", self.batch_output.text())
        settings.setValue("pages/batch/style_mode", self.style_radio.isChecked())
        settings.setValue("pages/batch/dpi", self.batch_dpi.text())
        settings.setValue("pages/batch/force", self.batch_force.isChecked())
        settings.setValue("pages/batch/keep_masters", self.batch_keep_masters.isChecked())

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.batch_input.setText(str(settings.value("pages/batch/input_dir", self.batch_input.text())))
        self.batch_output.setText(str(settings.value("pages/batch/output_dir", self.batch_output.text())))
        if settings.value("pages/batch/style_mode", True, type=bool):
            self.style_radio.setChecked(True)
        else:
            self.pil_radio.setChecked(True)
        self.batch_dpi.setText(str(settings.value("pages/batch/dpi", self.batch_dpi.text())))
        self.batch_force.setChecked(settings.value("pages/batch/force", False, type=bool))
        self.batch_keep_masters.setChecked(
            settings.value("pages/batch/keep_masters", False, type=bool)
        )
