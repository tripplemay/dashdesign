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
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui import api_config, cloud_bootstrap, theme

_THEME_MODES = (("system", "跟随系统"), ("light", "浅色"), ("dark", "深色"))


class SettingsDialog(QDialog):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 内容放滚动区、按钮固定底部：管理员展开云端配置后条目很多，小屏
        # （768 高）上否则 Save/Cancel 会被挤出屏幕外无法点击。
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._build_appearance_group())
        if not cloud_bootstrap.is_configured():
            # 非云端（dev / 自托管）才显示本机 API 配置；云端模式下密钥由
            # 管理员统一下发，普通用户无需也不该在本机各填一份。
            content_layout.addWidget(self._build_local_api_group())
        content_layout.addWidget(self._build_admin_cloud_group())
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("关闭")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip(
            "放弃本次外观改动并关闭（云端/本机配置由各自的保存按钮独立提交）"
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self._on_cancel)
        layout.addWidget(buttons)

        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            self.resize(min(600, avail.width() - 80), min(620, avail.height() - 80))

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

    # -- local API override (dev / self-hosted, no cloud) ----------------
    def _build_local_api_group(self) -> QGroupBox:
        group = QGroupBox("图像 API（本机）")
        grid = QGridLayout(group)
        hint = QLabel("本机未接入云端配置，请在此填写图像 API 端点与密钥。")
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        grid.addWidget(hint, 0, 0, 1, 2)
        self.local_api_base = QLineEdit(api_config.load_base_url())
        self.local_api_base.setPlaceholderText("如 https://api.example.com/v1")
        self.local_api_key = QLineEdit(api_config.load_api_key())
        self.local_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_api_key.setPlaceholderText("API Key")
        self.local_model = QLineEdit(api_config.load_baseline_model())
        self.local_model.setPlaceholderText("文档合并模型，如 gpt-4o")
        save_btn = QPushButton("保存本机配置")
        save_btn.clicked.connect(self._save_local_api)
        grid.addWidget(QLabel("API 端点"), 1, 0)
        grid.addWidget(self.local_api_base, 1, 1)
        grid.addWidget(QLabel("API Key"), 2, 0)
        grid.addWidget(self.local_api_key, 2, 1)
        grid.addWidget(QLabel("文本模型"), 3, 0)
        grid.addWidget(self.local_model, 3, 1)
        grid.addWidget(save_btn, 4, 1)
        grid.setColumnStretch(1, 1)
        self.local_api_status = QLabel("")
        self.local_api_status.setObjectName("Subtitle")
        grid.addWidget(self.local_api_status, 5, 0, 1, 2)
        return group

    def _save_local_api(self) -> None:
        base_url = self.local_api_base.text().strip()
        api_key = self.local_api_key.text().strip()
        if not base_url or not api_key:
            self.local_api_status.setText("API 端点与 Key 都不能为空。")
            return
        api_config.save(base_url, api_key, self.local_model.text().strip())
        self.local_api_status.setText("已保存到本机，立即生效。")

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

        divider = QLabel("— 修改管理密码 —")
        divider.setObjectName("Subtitle")
        grid.addWidget(divider, 5, 0, 1, 2)
        self.new_pw_edit = QLineEdit()
        self.new_pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_pw_edit.setPlaceholderText("新管理密码（至少 6 位）")
        self.confirm_pw_edit = QLineEdit()
        self.confirm_pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm_pw_edit.setPlaceholderText("再次输入新密码")
        self.change_pw_btn = QPushButton("修改管理密码")
        self.change_pw_btn.clicked.connect(self._change_password)
        # 表单内回车应提交当前操作，而不是触发对话框默认按钮直接关窗。
        self.new_pw_edit.returnPressed.connect(self._change_password)
        self.confirm_pw_edit.returnPressed.connect(self._change_password)
        grid.addWidget(QLabel("新管理密码"), 6, 0)
        grid.addWidget(self.new_pw_edit, 6, 1)
        grid.addWidget(QLabel("确认新密码"), 7, 0)
        grid.addWidget(self.confirm_pw_edit, 7, 1)
        grid.addWidget(self.change_pw_btn, 8, 1)
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

    def _change_password(self) -> None:
        current = self.admin_pw_edit.text().strip()
        new = self.new_pw_edit.text().strip()
        confirm = self.confirm_pw_edit.text().strip()
        if len(new) < 6:
            self.cloud_status.setText("新管理密码至少 6 位。")
            return
        if new != confirm:
            self.cloud_status.setText("两次输入的新密码不一致。")
            return
        try:
            cloud_bootstrap.change_admin_password(current, new)
        except PermissionError:
            self.cloud_status.setText("当前管理密码错误。")
            return
        except Exception as exc:  # noqa: BLE001
            self.cloud_status.setText(f"修改失败：{exc}")
            return
        # Keep editing with the new password (later saves use it).
        self.admin_pw_edit.setText(new)
        self.new_pw_edit.clear()
        self.confirm_pw_edit.clear()
        self.cloud_status.setText("管理密码已修改。请牢记新密码。")

    # -- dialog buttons -------------------------------------------------
    def _on_save(self) -> None:
        # Theme preview is already applied live; just keep it.
        self.accept()

    def _on_cancel(self) -> None:
        manager = theme.manager()
        if manager is not None and manager.mode() != self._original_mode:
            manager.set_mode(self._original_mode)
        self.reject()
