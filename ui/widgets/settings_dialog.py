"""Application settings dialog: API credentials + appearance.

Single home for user preferences. API base URL + key persist via
``ui.api_config``; the appearance (theme) section drives the shared
``ui.theme`` manager with live preview and cancel-to-revert.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ui import api_config, theme

_THEME_MODES = (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色"))


class SettingsDialog(QDialog):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(self._build_api_group())
        layout.addWidget(self._build_appearance_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

    def _build_api_group(self) -> QGroupBox:
        group = QGroupBox("API")
        grid = QGridLayout(group)
        self.base_url_edit = QLineEdit(api_config.load_base_url())
        self.base_url_edit.setPlaceholderText("可选：OpenAI-compatible base URL")
        self.api_key_edit = QLineEdit(api_config.load_api_key())
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("必填：图像 API Key")
        self.model_edit = QLineEdit(api_config.load_baseline_model())
        self.model_edit.setPlaceholderText("文档合并用的文本模型，如 gpt-4o（需网关支持）")
        hint = QLabel(
            "填写后自动保存到本机（当前用户），下次启动自动填入，无需重复输入。"
            "留空 Base URL/Key 时回退到系统环境变量 OPENAI_BASE_URL / OPENAI_API_KEY。"
            "“文档合并模型”须填你的网关实际支持的模型名（本网关仅支持 OpenAI 系，如 gpt-4o）。"
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        grid.addWidget(QLabel("Base URL"), 0, 0)
        grid.addWidget(self.base_url_edit, 0, 1)
        grid.addWidget(QLabel("API Key"), 1, 0)
        grid.addWidget(self.api_key_edit, 1, 1)
        grid.addWidget(QLabel("文档合并模型"), 2, 0)
        grid.addWidget(self.model_edit, 2, 1)
        grid.addWidget(hint, 3, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        return group

    def _build_appearance_group(self) -> QGroupBox:
        group = QGroupBox("外观")
        box = QVBoxLayout(group)
        manager = theme.manager()
        self._original_mode = manager.mode() if manager is not None else "system"
        self._theme_group = QButtonGroup(self)
        self._theme_group.setExclusive(True)
        for mode, label in _THEME_MODES:
            radio = QRadioButton(label)
            radio.setChecked(mode == self._original_mode)
            radio.toggled.connect(lambda checked, m=mode: self._preview_theme(m) if checked else None)
            self._theme_group.addButton(radio)
            box.addWidget(radio)
        return group

    def _preview_theme(self, mode: str) -> None:
        # 实时预览；取消时在 _on_cancel 里恢复。
        manager = theme.manager()
        if manager is not None:
            manager.set_mode(mode)

    def _on_save(self) -> None:
        api_config.save(self.base_url_edit.text(), self.api_key_edit.text(), self.model_edit.text())
        self.accept()

    def _on_cancel(self) -> None:
        manager = theme.manager()
        if manager is not None and manager.mode() != self._original_mode:
            manager.set_mode(self._original_mode)
        self.reject()
