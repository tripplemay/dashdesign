"""Shared API settings group used by the text-image and GPT pages."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QGroupBox, QLabel, QLineEdit, QWidget


class ApiSettingsGroup(QGroupBox):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__("API 设置", parent)
        layout = QGridLayout(self)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("可选：OpenAI-compatible base URL")
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("可选：仅本次进程使用，不写入文件")
        hint = QLabel(
            "留空时使用系统环境变量 OPENAI_BASE_URL / OPENAI_API_KEY。"
            "API Key 只注入本次运行的子进程，不会保存。"
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        layout.addWidget(QLabel("Base URL"), 0, 0)
        layout.addWidget(self.base_url_edit, 0, 1)
        layout.addWidget(QLabel("API Key"), 1, 0)
        layout.addWidget(self.api_key_edit, 1, 1)
        layout.addWidget(hint, 2, 0, 1, 2)
        layout.setColumnStretch(1, 1)

    def base_url(self) -> str:
        return self.base_url_edit.text()

    def api_key(self) -> str:
        return self.api_key_edit.text()

    def set_base_url(self, value: str) -> None:
        self.base_url_edit.setText(value)
