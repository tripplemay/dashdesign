"""QR-code area removal page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from app_runtime import PROJECT_ROOT
from ui.commands import QrForm
from ui.widgets import PathField


class QrPage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
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

    def form(self) -> QrForm:
        return QrForm(
            source=self.qr_input.text(),
            output_dir=self.qr_output.text(),
            box=self.qr_box.text(),
            reference_size=self.qr_reference_size.text(),
            margin=self.qr_margin.text(),
            radius=self.qr_radius.text(),
        )

    def input_preview_path(self) -> "Path | None":
        path = Path(self.qr_input.text()).expanduser()
        return path if path.exists() else None
