"""QR-code area removal page with on-preview rectangle selection."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui import workspace
from ui.output_paths import default_output, restore_output
from ui.commands import QrForm
from ui.utils import scrollable_page_layout
from ui.widgets import PathField


class QrPage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = scrollable_page_layout(self)

        paths = QGroupBox("输入输出")
        path_layout = QVBoxLayout(paths)
        self.qr_input = PathField("输入图片", "", "file", placeholder="拖入或选择需要清除二维码的图片")
        self.qr_output = PathField("输出目录", default_output("single_no_qr_desktop_qt"), "dir")
        self.workspace_note = QLabel("已启用工作区：成品图自动保存到「工作区 / 去二维码」。")
        self.workspace_note.setObjectName("Subtitle")
        self.workspace_note.setWordWrap(True)
        path_layout.addWidget(self.qr_input)
        path_layout.addWidget(self.qr_output)
        path_layout.addWidget(self.workspace_note)
        layout.addWidget(paths)

        params = QGroupBox("清除区域")
        params_layout = QGridLayout(params)
        self.select_button = QPushButton("在预览图上框选")
        self.select_button.setCheckable(True)
        self.select_button.setToolTip("点击后在右侧预览图上按住左键拖出矩形，松开自动回填坐标。")
        params_layout.addWidget(self.select_button, 0, 1)
        select_hint = QLabel("框选后自动填入坐标和参考尺寸；也可以手工输入像素坐标。")
        select_hint.setObjectName("Subtitle")
        select_hint.setWordWrap(True)
        params_layout.addWidget(select_hint, 1, 1)

        self.qr_box = QLineEdit()
        self.qr_box.setPlaceholderText("x1,y1,x2,y2（原图像素坐标）")
        self.qr_reference_size = QLineEdit()
        self.qr_reference_size.setPlaceholderText("可选：框选时自动填写，通常无需修改")
        self.qr_reference_size.setToolTip(
            "高级：如果坐标是从另一张大小不同的图片上量出来的，"
            "在此填那张图的宽x高（如 3238x1295），会自动按比例换算到本图。"
        )
        self.qr_margin = QDoubleSpinBox()
        self.qr_margin.setRange(0.0, 2.0)
        self.qr_margin.setSingleStep(0.05)
        self.qr_margin.setDecimals(2)
        self.qr_margin.setValue(0.55)
        self.qr_margin.setToolTip("向外扩大清除范围的比例：0.55 表示每边各多清除 55% 的框宽/高，用于盖住二维码周围的留白和文字。")
        self.qr_radius = QSpinBox()
        self.qr_radius.setRange(1, 64)
        self.qr_radius.setValue(21)
        self.qr_radius.setToolTip("清除区域边缘的柔化程度：越大过渡越自然，太大会发糊。")
        params_layout.addWidget(QLabel("区域"), 2, 0)
        params_layout.addWidget(self.qr_box, 2, 1)
        params_layout.addWidget(QLabel("参考尺寸"), 3, 0)
        params_layout.addWidget(self.qr_reference_size, 3, 1)
        params_layout.addWidget(QLabel("边界比例"), 4, 0)
        params_layout.addWidget(self.qr_margin, 4, 1)
        params_layout.addWidget(QLabel("边缘柔化"), 5, 0)
        params_layout.addWidget(self.qr_radius, 5, 1)
        params_layout.setColumnStretch(1, 1)
        layout.addWidget(params)
        layout.addStretch(1)
        self.refresh_workspace()

    def refresh_workspace(self) -> None:
        workspace.apply_output_field(self.qr_output, self.workspace_note)

    def apply_selection(self, rect: QRect, source_size: QSize) -> None:
        self.qr_box.setText(
            f"{rect.x()},{rect.y()},{rect.x() + rect.width()},{rect.y() + rect.height()}"
        )
        self.qr_reference_size.setText(f"{source_size.width()}x{source_size.height()}")

    def form(self) -> QrForm:
        return QrForm(
            source=self.qr_input.text(),
            output_dir=workspace.effective_output_dir(
                default_output("single_no_qr_desktop_qt"),
                self.qr_output.text(),
            ),
            box=self.qr_box.text(),
            reference_size=self.qr_reference_size.text(),
            margin=str(self.qr_margin.value()),
            radius=str(self.qr_radius.value()),
        )

    def input_preview_path(self) -> "Path | None":
        if not self.qr_input.text():
            return None
        path = Path(self.qr_input.text()).expanduser()
        return path if path.exists() else None

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/qr/output_dir", self.qr_output.text())
        settings.setValue("pages/qr/margin", self.qr_margin.value())
        settings.setValue("pages/qr/radius", self.qr_radius.value())

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.qr_output.setText(
            restore_output(str(settings.value("pages/qr/output_dir", "")), "single_no_qr_desktop_qt")
        )
        self.qr_margin.setValue(settings.value("pages/qr/margin", self.qr_margin.value(), type=float))
        self.qr_radius.setValue(settings.value("pages/qr/radius", self.qr_radius.value(), type=int))