"""Labelled path input with a file/directory picker button."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from app_runtime import PROJECT_ROOT


class PathField(QWidget):
    def __init__(
        self,
        label: str,
        value: str = "",
        mode: str = "file",
        placeholder: str = "",
        parent: "QWidget | None" = None,
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
