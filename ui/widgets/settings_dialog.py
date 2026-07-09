"""Application settings dialog: appearance (everyone) + cloud config (admin only).

Ordinary users only see the appearance section — the image-API endpoint/key and
baseline endpoint are fetched from the cloud automatically, so there is nothing
for them to set. An admin unlocks the cloud-config section with the admin
password, edits it, and uploads it; every client picks it up on the next fetch.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ui import cloud_bootstrap, theme

_THEME_MODES = (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色"))


class SettingsDialog(QDialog):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        layout.addWidget(self._build_appearance_group())
        layout.addWidget(self._build_admin_cloud_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("关闭")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

    # -- appearance (everyone) -----------------------------------------
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
        manager = theme.manager()
        if manager is not None:
            manager.set_mode(mode)

    # -- cloud config (admin only) -------------------------------------
    def _build_admin_cloud_group(self) -> QGroupBox:
        group = QGroupBox("云端配置（仅管理员）")
        outer = QVBoxLayout(group)

        hint = QLabel(
            "普通用户无需任何设置：图像 API 与基线端点会自动从云端获取。"
            "管理员在此配置并上传，所有人下次启动自动生效。"
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        gate = QHBoxLayout()
        self.admin_pw_edit = QLineEdit()
        self.admin_pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.admin_pw_edit.setPlaceholderText("管理密码")
        self.admin_pw_edit.returnPressed.connect(self._unlock)
        self.unlock_btn = QPushButton("解锁")
        self.unlock_btn.clicked.connect(self._unlock)
        gate.addWidget(QLabel("管理密码"))
        gate.addWidget(self.admin_pw_edit, 1)
        gate.addWidget(self.unlock_btn)
        outer.addLayout(gate)

        self.cfg_container = QWidget()
        grid = QGridLayout(self.cfg_container)
        grid.setContentsMargins(0, 6, 0, 0)
        cfg = cloud_bootstrap.cached_app_config()
        self.cfg_baseline_ep = QLineEdit(str(cfg.get("baseline_endpoint", "") or ""))
        self.cfg_baseline_ep.setPlaceholderText("留空 = 使用默认云端地址")
        self.cfg_api_base = QLineEdit(str(cfg.get("image_api_base_url", "") or ""))
        self.cfg_api_base.setPlaceholderText("图像 API Base URL")
        self.cfg_api_key = QLineEdit(str(cfg.get("image_api_key", "") or ""))
        self.cfg_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.cfg_api_key.setPlaceholderText("图像 API Key")
        self.cfg_model = QLineEdit(str(cfg.get("baseline_model", "") or "gpt-4o"))
        self.cfg_model.setPlaceholderText("文档合并模型，如 gpt-4o")
        self.save_cloud_btn = QPushButton("保存并上传云端")
        self.save_cloud_btn.clicked.connect(self._save_cloud)
        grid.addWidget(QLabel("基线端点"), 0, 0)
        grid.addWidget(self.cfg_baseline_ep, 0, 1)
        grid.addWidget(QLabel("图像 API 端点"), 1, 0)
        grid.addWidget(self.cfg_api_base, 1, 1)
        grid.addWidget(QLabel("图像 API Key"), 2, 0)
        grid.addWidget(self.cfg_api_key, 2, 1)
        grid.addWidget(QLabel("文档合并模型"), 3, 0)
        grid.addWidget(self.cfg_model, 3, 1)
        grid.addWidget(self.save_cloud_btn, 4, 1)
        grid.setColumnStretch(1, 1)
        self.cfg_container.setEnabled(False)
        outer.addWidget(self.cfg_container)

        self.cloud_status = QLabel("")
        self.cloud_status.setObjectName("Subtitle")
        self.cloud_status.setWordWrap(True)
        outer.addWidget(self.cloud_status)
        return group

    def _unlock(self) -> None:
        password = self.admin_pw_edit.text().strip()
        if not password:
            self.cloud_status.setText("请输入管理密码。")
            return
        try:
            ok = cloud_bootstrap.verify_admin(password)
        except Exception as exc:  # noqa: BLE001
            self.cloud_status.setText(f"无法连接云端：{exc}")
            return
        if not ok:
            self.cloud_status.setText("管理密码错误。")
            return
        self.cfg_container.setEnabled(True)
        self.unlock_btn.setEnabled(False)
        self.admin_pw_edit.setEnabled(False)
        cfg = cloud_bootstrap.fetch_app_config()
        self.cfg_baseline_ep.setText(str(cfg.get("baseline_endpoint", "") or ""))
        self.cfg_api_base.setText(str(cfg.get("image_api_base_url", "") or ""))
        self.cfg_api_key.setText(str(cfg.get("image_api_key", "") or ""))
        self.cfg_model.setText(str(cfg.get("baseline_model", "") or "gpt-4o"))
        self.cloud_status.setText("已解锁，可编辑并上传。")

    def _save_cloud(self) -> None:
        password = self.admin_pw_edit.text().strip()
        config = {
            "baseline_endpoint": self.cfg_baseline_ep.text().strip(),
            "image_api_base_url": self.cfg_api_base.text().strip(),
            "image_api_key": self.cfg_api_key.text().strip(),
            "baseline_model": self.cfg_model.text().strip() or "gpt-4o",
        }
        try:
            cloud_bootstrap.push_app_config(password, config)
        except PermissionError:
            self.cloud_status.setText("管理密码错误。")
            return
        except Exception as exc:  # noqa: BLE001
            self.cloud_status.setText(f"上传失败：{exc}")
            return
        # A changed baseline endpoint must swap the active repository.
        from ui import baseline_service

        baseline_service.reset_repository()
        self.cloud_status.setText("已保存并上传，所有用户下次启动自动生效。")

    # -- dialog buttons -------------------------------------------------
    def _on_save(self) -> None:
        # Theme preview is already applied live; just keep it.
        self.accept()

    def _on_cancel(self) -> None:
        manager = theme.manager()
        if manager is not None and manager.mode() != self._original_mode:
            manager.set_mode(self._original_mode)
        self.reject()
