"""Application-wide API settings dialog (base URL + key), persisted."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ui import api_config


class ApiSettingsDialog(QDialog):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("API 设置")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        grid = QGridLayout()
        self.base_url_edit = QLineEdit(api_config.load_base_url())
        self.base_url_edit.setPlaceholderText("可选：OpenAI-compatible base URL")
        self.api_key_edit = QLineEdit(api_config.load_api_key())
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("必填：图像 API Key")
        grid.addWidget(QLabel("Base URL"), 0, 0)
        grid.addWidget(self.base_url_edit, 0, 1)
        grid.addWidget(QLabel("API Key"), 1, 0)
        grid.addWidget(self.api_key_edit, 1, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        hint = QLabel(
            "填写后自动保存到本机（当前用户），下次启动自动填入，无需重复输入。"
            "留空时回退到系统环境变量 OPENAI_BASE_URL / OPENAI_API_KEY。"
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        api_config.save(self.base_url_edit.text(), self.api_key_edit.text())
        self.accept()
