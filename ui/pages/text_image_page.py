"""Text-to-image page: background / local poster / full poster modes.

Mode-specific controls are shown/hidden (not merely disabled) so the form
only ever presents fields that matter for the selected mode.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app_runtime import PROJECT_ROOT
from ui import api_config, baseline_service
from ui.commands import TextImageForm
from ui.scene_prompts import ScenePrompt, load_scene_prompts
from ui.utils import scrollable_page_layout
from ui.widgets import FlowLayout, PathField

_PROMPT_PLACEHOLDER_DEFAULT = "只描述背景、场景、主体、氛围和构图要求，不粘贴完整海报文案。"
_PROMPT_PLACEHOLDER_FULL = "可选：补充完整海报的主体、场景或特别要求。留空时将使用模板、基线和文案生成。"

_MODE_HINTS = {
    "background": "模型只生成无文字背景图，之后可在批量印刷中输出印刷规格。",
    "full_poster": "gpt-image-2 直接生成含中文排版的完整海报，输出多个候选，需人工逐字核对文字。",
}


def _mark_invalid(widget, invalid: bool) -> None:  # type: ignore[no-untyped-def]
    widget.setProperty("invalid", "true" if invalid else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class TextImagePage(QWidget):
    # 后台解析当前基线完成后回主线程（跨线程 emit -> QueuedConnection）。
    _activeBaselineReady = Signal(str, int)

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = scrollable_page_layout(self)

        # --- 生成模式 -------------------------------------------------
        mode_group = QGroupBox("生成模式")
        mode_layout = QVBoxLayout(mode_group)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        self.t2i_mode = QComboBox()
        self.t2i_mode.addItem("无文字背景", "background")
        self.t2i_mode.addItem("完整海报 Image2", "full_poster")
        self.t2i_mode.currentIndexChanged.connect(self.sync_text_image_mode)
        mode_row.addWidget(QLabel("输出类型"))
        mode_row.addWidget(self.t2i_mode)
        mode_row.addStretch(1)
        mode_layout.addLayout(mode_row)
        self.mode_hint = QLabel("")
        self.mode_hint.setObjectName("Subtitle")
        self.mode_hint.setWordWrap(True)
        mode_layout.addWidget(self.mode_hint)
        layout.addWidget(mode_group)

        # --- 画面提示词 ------------------------------------------------
        self.prompt_box = QGroupBox("画面提示词（必填）")
        prompt_layout = QVBoxLayout(self.prompt_box)
        self.t2i_prompt = QPlainTextEdit()
        self.t2i_prompt.setObjectName("TextPrompt")
        self.t2i_prompt.setPlaceholderText(_PROMPT_PLACEHOLDER_DEFAULT)
        self.t2i_prompt.setMinimumHeight(110)
        self.t2i_prompt.textChanged.connect(lambda: _mark_invalid(self.t2i_prompt, False))
        # 快捷模板：内置儿童教育行业画面提示词，点击追加到文本框（用户可再手改）。
        # 显示中文；生成脚本会在调用图像模型前把画面提示词中译英。
        self._scene_prompts = load_scene_prompts()
        if self._scene_prompts:
            chips_hint = QLabel("快捷模板（点击追加，内容可手动修改）")
            chips_hint.setObjectName("Subtitle")
            prompt_layout.addWidget(chips_hint)
            chips_container = QWidget()
            chips_flow = FlowLayout(chips_container, margin=0, spacing=6)
            for scene in self._scene_prompts:
                chip = QPushButton(scene.label)
                chip.setToolTip(scene.prompt)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.clicked.connect(lambda _checked=False, s=scene: self._append_scene_prompt(s))
                chips_flow.addWidget(chip)
            prompt_layout.addWidget(chips_container)
        prompt_layout.addWidget(self.t2i_prompt)
        layout.addWidget(self.prompt_box)

        # --- 海报文案（仅 full_poster） -------------------------------
        self.t2i_copy_box = QGroupBox("海报文案（必填）")
        copy_layout = QVBoxLayout(self.t2i_copy_box)
        self.t2i_copy = QPlainTextEdit()
        self.t2i_copy.setObjectName("TextPrompt")
        self.t2i_copy.setPlaceholderText("粘贴：主标题、副标题、课程类型/模块、结语/行动语。")
        self.t2i_copy.setMinimumHeight(100)
        self.t2i_copy.textChanged.connect(lambda: _mark_invalid(self.t2i_copy, False))
        copy_layout.addWidget(self.t2i_copy)
        layout.addWidget(self.t2i_copy_box)

        # --- 完整海报模板（仅 full_poster） ---------------------------
        self.template_group = QGroupBox("完整海报模板")
        template_layout = QVBoxLayout(self.template_group)
        template_layout.setSpacing(10)
        self.t2i_purpose_template = QComboBox()
        self.t2i_purpose_template.addItem("课程招生海报", "course_enrollment")
        self.t2i_purpose_template.addItem("免费试听/体验课", "trial_class")
        self.t2i_purpose_template.addItem("AI能力测评预约", "ability_assessment")
        self.t2i_purpose_template.addItem("课程体系介绍", "course_system")
        self.t2i_purpose_template.setMinimumWidth(150)
        self.t2i_style_template = QComboBox()
        self.t2i_style_template.addItem("科技霓虹", "tech_neon")
        self.t2i_style_template.addItem("明亮少儿教育", "bright_edu")
        self.t2i_style_template.addItem("梦幻AI绘图", "fantasy_ai_art")
        self.t2i_style_template.addItem("高端简洁", "premium_minimal")
        self.t2i_style_template.addItem("漫画热血", "comic_pop")
        self.t2i_style_template.setMinimumWidth(150)
        self.t2i_layout_template = QComboBox()
        self.t2i_layout_template.addItem("顶部标题+模块+CTA", "headline_modules_cta")
        self.t2i_layout_template.addItem("中心主体+环绕模块", "central_subject_orbit_modules")
        self.t2i_layout_template.addItem("竖版展架信息流", "portrait_exhibition")
        self.t2i_layout_template.addItem("方版社媒主视觉", "square_social")
        self.t2i_layout_template.setMinimumWidth(160)
        self.t2i_text_density = QComboBox()
        self.t2i_text_density.addItem("中文字", "medium")
        self.t2i_text_density.addItem("低文字", "low")
        self.t2i_text_density.addItem("高文字", "high")
        self.t2i_text_density.setMinimumWidth(100)

        template_row = QHBoxLayout()
        template_row.setSpacing(8)
        template_row.addWidget(QLabel("用途"))
        template_row.addWidget(self.t2i_purpose_template)
        template_row.addSpacing(12)
        template_row.addWidget(QLabel("风格"))
        template_row.addWidget(self.t2i_style_template)
        template_row.addStretch(1)
        template_layout.addLayout(template_row)

        layout_row = QHBoxLayout()
        layout_row.setSpacing(8)
        layout_row.addWidget(QLabel("构图"))
        layout_row.addWidget(self.t2i_layout_template)
        layout_row.addSpacing(12)
        layout_row.addWidget(QLabel("文字密度"))
        layout_row.addWidget(self.t2i_text_density)
        layout_row.addSpacing(12)
        self.t2i_candidates = QSpinBox()
        self.t2i_candidates.setRange(1, 8)
        self.t2i_candidates.setValue(4)
        self.t2i_candidates.setToolTip("一次生成的候选海报数量；候选越多可挑选空间越大，费用也越高。")
        layout_row.addWidget(QLabel("候选数"))
        layout_row.addWidget(self.t2i_candidates)
        layout_row.addStretch(1)
        template_layout.addLayout(layout_row)

        style_row = QHBoxLayout()
        style_row.setSpacing(8)
        self.t2i_full_style = QLineEdit("")
        self.t2i_full_style.setPlaceholderText("可选：补充模板之外的画面/字体/气质要求")
        style_row.addWidget(QLabel("补充要求"))
        style_row.addWidget(self.t2i_full_style, 1)
        template_layout.addLayout(style_row)
        layout.addWidget(self.template_group)

        # --- 印刷尺寸与模型参数 ----------------------------------------
        params = QGroupBox("印刷尺寸与模型参数")
        params_layout = QVBoxLayout(params)
        params_layout.setSpacing(10)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.t2i_width_cm = QDoubleSpinBox()
        self.t2i_width_cm.setRange(1, 1000)
        self.t2i_width_cm.setDecimals(1)
        self.t2i_width_cm.setValue(120)
        self.t2i_height_cm = QDoubleSpinBox()
        self.t2i_height_cm.setRange(1, 1000)
        self.t2i_height_cm.setDecimals(1)
        self.t2i_height_cm.setValue(80)
        self.t2i_dpi = QSpinBox()
        self.t2i_dpi.setRange(30, 600)
        self.t2i_dpi.setValue(200)
        self.t2i_dpi.setToolTip("印刷输出分辨率：写真/展架常用 200，大幅喷绘可用 150。")
        size_row.addWidget(QLabel("宽 cm"))
        size_row.addWidget(self.t2i_width_cm)
        size_row.addSpacing(12)
        size_row.addWidget(QLabel("高 cm"))
        size_row.addWidget(self.t2i_height_cm)
        size_row.addSpacing(12)
        size_row.addWidget(QLabel("DPI"))
        size_row.addWidget(self.t2i_dpi)
        size_row.addStretch(1)
        params_layout.addLayout(size_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self.t2i_image_size = QComboBox()
        self.t2i_image_size.addItems(["auto", "1536x1024", "1024x1536", "1536x1536", "1024x1024"])
        self.t2i_image_size.setMinimumWidth(130)
        self.t2i_image_size.setToolTip(
            "模型生成的母版像素（auto 会按印刷宽高比自动选择）。\n"
            "母版之后会被放大到印刷像素，比例不符时用模糊扩边补齐。"
        )
        self.t2i_quality = QComboBox()
        self.t2i_quality.addItems(["high", "medium", "low", "auto"])
        self.t2i_quality.setMinimumWidth(110)
        model_row.addWidget(QLabel("模型尺寸"))
        model_row.addWidget(self.t2i_image_size)
        model_row.addSpacing(12)
        model_row.addWidget(QLabel("质量"))
        model_row.addWidget(self.t2i_quality)
        model_row.addStretch(1)
        params_layout.addLayout(model_row)

        option_row = QHBoxLayout()
        option_row.setSpacing(18)
        self.t2i_postprocess = QCheckBox("生成后输出印刷尺寸")
        self.t2i_postprocess.setChecked(True)
        option_row.addWidget(self.t2i_postprocess)
        option_row.addStretch(1)
        params_layout.addLayout(option_row)
        layout.addWidget(params)

        # --- 输出 -----------------------------------------------------
        paths = QGroupBox("输出")
        path_layout = QVBoxLayout(paths)
        self.t2i_output = PathField(
            "输出目录",
            str(PROJECT_ROOT / "workflow_samples" / "text_to_image_print_qt"),
            "dir",
        )
        self.baseline_label = QLabel("")
        self.baseline_label.setObjectName("Subtitle")
        self.baseline_label.setWordWrap(True)
        self.baseline_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_layout.addWidget(self.t2i_output)
        path_layout.addWidget(self.baseline_label)
        layout.addWidget(paths)

        layout.addStretch(1)
        self._active_seq = 0
        self._activeBaselineReady.connect(self._apply_active_baseline)
        self.refresh_active_baseline()
        self.sync_text_image_mode()

    def refresh_active_baseline(self) -> None:
        """异步解析当前项目基线（走网络），避免切到本页时卡住 UI。"""
        self._active_seq += 1
        seq = self._active_seq
        self.baseline_label.setText("当前项目基线：加载中…")

        def worker() -> None:
            try:
                info = baseline_service.active_project_id() or "（未选择）"
                path = str(baseline_service.active_baseline_path())
                text = f"当前项目基线：{info} · {path}"
            except Exception as exc:  # noqa: BLE001 — degrade to a message, never crash the page
                text = f"当前项目基线：加载失败（{exc}）"
            self._activeBaselineReady.emit(text, seq)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_active_baseline(self, text: str, seq: int) -> None:
        if seq == self._active_seq:  # 只应用最新一次（避免旧结果覆盖新切换）
            self.baseline_label.setText(text)

    def _append_scene_prompt(self, scene: ScenePrompt) -> None:
        """点击快捷模板：在已有内容后换行追加该场景的中文画面提示词。"""
        current = self.t2i_prompt.toPlainText().rstrip()
        self.t2i_prompt.setPlainText(f"{current}\n{scene.prompt}" if current else scene.prompt)
        self.t2i_prompt.moveCursor(QTextCursor.MoveOperation.End)
        self.t2i_prompt.setFocus()
        _mark_invalid(self.t2i_prompt, False)

    def confirm_run(self, window) -> bool:  # type: ignore[no-untyped-def]
        if not api_config.has_api_key():
            window.banner.show_message(
                "error",
                "尚未配置 API Key，无法调用图像 API。请先在“文件 → 设置”中填写。",
                action_label="打开设置",
                action_callback=window.open_settings,
            )
            return False
        return True

    # ------------------------------------------------------------------
    def sync_text_image_mode(self) -> None:
        mode = str(self.t2i_mode.currentData() or "background")
        full_poster_mode = mode == "full_poster"

        self.mode_hint.setText(_MODE_HINTS.get(mode, ""))
        self.t2i_copy_box.setVisible(full_poster_mode)
        self.template_group.setVisible(full_poster_mode)

        if full_poster_mode:
            self.prompt_box.setTitle("画面提示词（可选）")
            self.t2i_prompt.setPlaceholderText(_PROMPT_PLACEHOLDER_FULL)
        else:
            self.prompt_box.setTitle("画面提示词（必填）")
            self.t2i_prompt.setPlaceholderText(_PROMPT_PLACEHOLDER_DEFAULT)
        _mark_invalid(self.t2i_prompt, False)
        _mark_invalid(self.t2i_copy, False)

    def on_validation_error(self) -> None:
        mode = str(self.t2i_mode.currentData() or "background")
        prompt_missing = mode != "full_poster" and not self.t2i_prompt.toPlainText().strip()
        copy_missing = mode == "full_poster" and not self.t2i_copy.toPlainText().strip()
        _mark_invalid(self.t2i_prompt, prompt_missing)
        _mark_invalid(self.t2i_copy, copy_missing)

    def form(self) -> TextImageForm:
        return TextImageForm(
            output_dir=self.t2i_output.text(),
            baseline_path=str(baseline_service.active_baseline_path()),
            prompt=self.t2i_prompt.toPlainText(),
            mode=str(self.t2i_mode.currentData() or "background"),
            poster_copy=self.t2i_copy.toPlainText(),
            width_cm=str(self.t2i_width_cm.value()),
            height_cm=str(self.t2i_height_cm.value()),
            dpi=str(self.t2i_dpi.value()),
            candidates=str(self.t2i_candidates.value()),
            full_style=self.t2i_full_style.text(),
            purpose_template=str(self.t2i_purpose_template.currentData() or "course_enrollment"),
            style_template=str(self.t2i_style_template.currentData() or "tech_neon"),
            layout_template=str(self.t2i_layout_template.currentData() or "headline_modules_cta"),
            text_density=str(self.t2i_text_density.currentData() or "medium"),
            image_size=self.t2i_image_size.currentText(),
            quality=self.t2i_quality.currentText(),
            postprocess=self.t2i_postprocess.isChecked(),
            base_url=api_config.load_base_url(),
            api_key=api_config.load_api_key(),
            text_model=api_config.load_baseline_model(),
        )

    def input_preview_path(self) -> "Path | None":
        return None

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/t2i/output_dir", self.t2i_output.text())
        settings.setValue("pages/t2i/mode", str(self.t2i_mode.currentData()))
        settings.setValue("pages/t2i/purpose_template", str(self.t2i_purpose_template.currentData()))
        settings.setValue("pages/t2i/style_template", str(self.t2i_style_template.currentData()))
        settings.setValue("pages/t2i/layout_template", str(self.t2i_layout_template.currentData()))
        settings.setValue("pages/t2i/text_density", str(self.t2i_text_density.currentData()))
        settings.setValue("pages/t2i/candidates", self.t2i_candidates.value())
        settings.setValue("pages/t2i/width_cm", self.t2i_width_cm.value())
        settings.setValue("pages/t2i/height_cm", self.t2i_height_cm.value())
        settings.setValue("pages/t2i/dpi", self.t2i_dpi.value())
        settings.setValue("pages/t2i/image_size", self.t2i_image_size.currentText())
        settings.setValue("pages/t2i/quality", self.t2i_quality.currentText())
        settings.setValue("pages/t2i/postprocess", self.t2i_postprocess.isChecked())
        # 提示词/文案属于单次运行输入，不持久化；API 凭据由 ui.api_config 统一持久化。

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.t2i_output.setText(str(settings.value("pages/t2i/output_dir", self.t2i_output.text())))
        for combo, key in (
            (self.t2i_mode, "pages/t2i/mode"),
            (self.t2i_purpose_template, "pages/t2i/purpose_template"),
            (self.t2i_style_template, "pages/t2i/style_template"),
            (self.t2i_layout_template, "pages/t2i/layout_template"),
            (self.t2i_text_density, "pages/t2i/text_density"),
        ):
            value = settings.value(key)
            if value is not None:
                index = combo.findData(str(value))
                if index >= 0:
                    combo.setCurrentIndex(index)
        self.t2i_candidates.setValue(settings.value("pages/t2i/candidates", self.t2i_candidates.value(), type=int))
        self.t2i_width_cm.setValue(settings.value("pages/t2i/width_cm", self.t2i_width_cm.value(), type=float))
        self.t2i_height_cm.setValue(settings.value("pages/t2i/height_cm", self.t2i_height_cm.value(), type=float))
        self.t2i_dpi.setValue(settings.value("pages/t2i/dpi", self.t2i_dpi.value(), type=int))
        for combo, key in (
            (self.t2i_image_size, "pages/t2i/image_size"),
            (self.t2i_quality, "pages/t2i/quality"),
        ):
            value = settings.value(key)
            if value is not None:
                index = combo.findText(str(value))
                if index >= 0:
                    combo.setCurrentIndex(index)
        self.t2i_postprocess.setChecked(settings.value("pages/t2i/postprocess", True, type=bool))
        self.sync_text_image_mode()
