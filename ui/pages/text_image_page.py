"""Text-to-image page: background / local poster / full poster modes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app_runtime import PROJECT_ROOT, baseline_path
from ui.commands import TextImageForm
from ui.widgets import PathField


class TextImagePage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        paths = QGroupBox("基线与输出")
        path_layout = QVBoxLayout(paths)
        self.t2i_output = PathField(
            "输出目录",
            str(PROJECT_ROOT / "workflow_samples" / "text_to_image_print_qt"),
            "dir",
        )
        baseline_label = QLabel(f"基线文件：{baseline_path()}")
        baseline_label.setWordWrap(True)
        baseline_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_layout.addWidget(baseline_label)
        path_layout.addWidget(self.t2i_output)
        layout.addWidget(paths)

        prompt_box = QGroupBox("画面提示词")
        prompt_layout = QVBoxLayout(prompt_box)
        self.t2i_prompt = QPlainTextEdit()
        self.t2i_prompt.setObjectName("TextPrompt")
        self.t2i_prompt.setPlaceholderText("只描述背景、场景、主体、氛围和构图要求，不粘贴完整海报文案。")
        self.t2i_prompt.setMinimumHeight(130)
        prompt_layout.addWidget(self.t2i_prompt)
        layout.addWidget(prompt_box)

        self.t2i_copy_box = QGroupBox("海报文案（带文字海报模式）")
        copy_layout = QVBoxLayout(self.t2i_copy_box)
        self.t2i_copy = QPlainTextEdit()
        self.t2i_copy.setObjectName("TextPrompt")
        self.t2i_copy.setPlaceholderText(
            "可粘贴：主标题、副标题、课程类型/模块、结语/行动语。背景模式会忽略这里。"
        )
        self.t2i_copy.setMinimumHeight(120)
        copy_layout.addWidget(self.t2i_copy)
        layout.addWidget(self.t2i_copy_box)

        params = QGroupBox("生成与印刷参数")
        params_layout = QVBoxLayout(params)
        params_layout.setSpacing(10)
        self.t2i_mode = QComboBox()
        self.t2i_mode.addItem("无文字背景", "background")
        self.t2i_mode.addItem("带文字海报（本地合成）", "poster")
        self.t2i_mode.addItem("完整海报 Image2", "full_poster")
        self.t2i_mode.currentIndexChanged.connect(self.sync_text_image_mode)
        self.t2i_text_style = QComboBox()
        self.t2i_text_style.addItem("清爽教育", "clean_edu")
        self.t2i_text_style.addItem("科技霓虹", "tech_neon")
        self.t2i_text_style.setMinimumWidth(130)
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
        self.t2i_full_style = QLineEdit("")
        self.t2i_full_style.setPlaceholderText("可选：补充模板之外的画面/字体/气质要求")
        self.t2i_candidates = QLineEdit("4")
        self.t2i_candidates.setFixedWidth(76)
        self.t2i_width_cm = QLineEdit("120")
        self.t2i_height_cm = QLineEdit("80")
        self.t2i_dpi = QLineEdit("200")
        for field in (self.t2i_width_cm, self.t2i_height_cm, self.t2i_dpi):
            field.setFixedWidth(76)
        self.t2i_image_size = QComboBox()
        self.t2i_image_size.addItems(["auto", "1536x1024", "1024x1536", "1536x1536", "1024x1024"])
        self.t2i_image_size.setMinimumWidth(130)
        self.t2i_quality = QComboBox()
        self.t2i_quality.addItems(["high", "medium", "low", "auto"])
        self.t2i_quality.setMinimumWidth(110)
        self.t2i_execute = QCheckBox("立即调用 API")
        self.t2i_postprocess = QCheckBox("生成后输出印刷尺寸")
        self.t2i_postprocess.setChecked(True)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        mode_row.addWidget(QLabel("输出类型"))
        mode_row.addWidget(self.t2i_mode)
        mode_row.addSpacing(12)
        mode_row.addWidget(QLabel("本地文字风格"))
        mode_row.addWidget(self.t2i_text_style)
        mode_row.addSpacing(12)
        mode_row.addWidget(QLabel("候选数"))
        mode_row.addWidget(self.t2i_candidates)
        mode_row.addStretch(1)
        params_layout.addLayout(mode_row)

        template_row = QHBoxLayout()
        template_row.setSpacing(8)
        template_row.addWidget(QLabel("用途模板"))
        template_row.addWidget(self.t2i_purpose_template)
        template_row.addSpacing(12)
        template_row.addWidget(QLabel("风格模板"))
        template_row.addWidget(self.t2i_style_template)
        template_row.addSpacing(12)
        template_row.addWidget(QLabel("文字密度"))
        template_row.addWidget(self.t2i_text_density)
        template_row.addStretch(1)
        params_layout.addLayout(template_row)

        layout_template_row = QHBoxLayout()
        layout_template_row.setSpacing(8)
        layout_template_row.addWidget(QLabel("构图模板"))
        layout_template_row.addWidget(self.t2i_layout_template)
        layout_template_row.addStretch(1)
        params_layout.addLayout(layout_template_row)

        full_style_row = QHBoxLayout()
        full_style_row.setSpacing(8)
        full_style_row.addWidget(QLabel("补充要求"))
        full_style_row.addWidget(self.t2i_full_style, 1)
        params_layout.addLayout(full_style_row)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
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
        model_row.addWidget(QLabel("模型尺寸"))
        model_row.addWidget(self.t2i_image_size)
        model_row.addSpacing(12)
        model_row.addWidget(QLabel("质量"))
        model_row.addWidget(self.t2i_quality)
        model_row.addStretch(1)
        params_layout.addLayout(model_row)

        option_row = QHBoxLayout()
        option_row.setSpacing(18)
        option_row.addWidget(self.t2i_execute)
        option_row.addWidget(self.t2i_postprocess)
        option_row.addStretch(1)
        params_layout.addLayout(option_row)
        layout.addWidget(params)

        api = QGroupBox("API 设置")
        api_layout = QGridLayout(api)
        self.t2i_base_url = QLineEdit()
        self.t2i_base_url.setPlaceholderText("可选：OpenAI-compatible base URL")
        self.t2i_api_key = QLineEdit()
        self.t2i_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.t2i_api_key.setPlaceholderText("可选：仅本次进程使用，不写入文件")
        api_layout.addWidget(QLabel("Base URL"), 0, 0)
        api_layout.addWidget(self.t2i_base_url, 0, 1)
        api_layout.addWidget(QLabel("API Key"), 1, 0)
        api_layout.addWidget(self.t2i_api_key, 1, 1)
        api_layout.setColumnStretch(1, 1)
        layout.addWidget(api)
        layout.addStretch(1)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)
        self.sync_text_image_mode()

    def sync_text_image_mode(self) -> None:
        if not hasattr(self, "t2i_mode") or not hasattr(self, "t2i_copy_box"):
            return
        mode = str(self.t2i_mode.currentData() or "background")
        poster_mode = mode in {"poster", "full_poster"}
        full_poster_mode = mode == "full_poster"
        self.t2i_copy_box.setEnabled(poster_mode)
        if hasattr(self, "t2i_text_style"):
            self.t2i_text_style.setEnabled(mode == "poster")
        if hasattr(self, "t2i_full_style"):
            self.t2i_full_style.setEnabled(full_poster_mode)
        if hasattr(self, "t2i_candidates"):
            self.t2i_candidates.setEnabled(full_poster_mode)
        for name in (
            "t2i_purpose_template",
            "t2i_style_template",
            "t2i_layout_template",
            "t2i_text_density",
        ):
            if hasattr(self, name):
                getattr(self, name).setEnabled(full_poster_mode)
        if hasattr(self, "t2i_prompt"):
            if full_poster_mode:
                self.t2i_prompt.setPlaceholderText(
                    "可选：补充完整海报的主体、场景或特别要求。留空时将使用模板、基线和文案生成。"
                )
            else:
                self.t2i_prompt.setPlaceholderText("只描述背景、场景、主体、氛围和构图要求，不粘贴完整海报文案。")

    def form(self) -> TextImageForm:
        return TextImageForm(
            output_dir=self.t2i_output.text(),
            prompt=self.t2i_prompt.toPlainText(),
            mode=str(self.t2i_mode.currentData() or "background"),
            poster_copy=self.t2i_copy.toPlainText(),
            width_cm=self.t2i_width_cm.text(),
            height_cm=self.t2i_height_cm.text(),
            dpi=self.t2i_dpi.text(),
            candidates=self.t2i_candidates.text(),
            full_style=self.t2i_full_style.text(),
            text_style=str(self.t2i_text_style.currentData() or "clean_edu"),
            purpose_template=str(self.t2i_purpose_template.currentData() or "course_enrollment"),
            style_template=str(self.t2i_style_template.currentData() or "tech_neon"),
            layout_template=str(self.t2i_layout_template.currentData() or "headline_modules_cta"),
            text_density=str(self.t2i_text_density.currentData() or "medium"),
            image_size=self.t2i_image_size.currentText(),
            quality=self.t2i_quality.currentText(),
            execute=self.t2i_execute.isChecked(),
            postprocess=self.t2i_postprocess.isChecked(),
            base_url=self.t2i_base_url.text(),
            api_key=self.t2i_api_key.text(),
        )

    def input_preview_path(self) -> "Path | None":
        return None

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/t2i/output_dir", self.t2i_output.text())
        settings.setValue("pages/t2i/mode", str(self.t2i_mode.currentData()))
        settings.setValue("pages/t2i/text_style", str(self.t2i_text_style.currentData()))
        settings.setValue("pages/t2i/purpose_template", str(self.t2i_purpose_template.currentData()))
        settings.setValue("pages/t2i/style_template", str(self.t2i_style_template.currentData()))
        settings.setValue("pages/t2i/layout_template", str(self.t2i_layout_template.currentData()))
        settings.setValue("pages/t2i/text_density", str(self.t2i_text_density.currentData()))
        settings.setValue("pages/t2i/candidates", self.t2i_candidates.text())
        settings.setValue("pages/t2i/width_cm", self.t2i_width_cm.text())
        settings.setValue("pages/t2i/height_cm", self.t2i_height_cm.text())
        settings.setValue("pages/t2i/dpi", self.t2i_dpi.text())
        settings.setValue("pages/t2i/image_size", self.t2i_image_size.currentText())
        settings.setValue("pages/t2i/quality", self.t2i_quality.currentText())
        settings.setValue("pages/t2i/postprocess", self.t2i_postprocess.isChecked())
        settings.setValue("pages/t2i/base_url", self.t2i_base_url.text())
        # 提示词/文案/API Key/立即调用 属于单次运行输入，刻意不持久化。

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.t2i_output.setText(str(settings.value("pages/t2i/output_dir", self.t2i_output.text())))
        for combo, key in (
            (self.t2i_mode, "pages/t2i/mode"),
            (self.t2i_text_style, "pages/t2i/text_style"),
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
        for edit, key in (
            (self.t2i_candidates, "pages/t2i/candidates"),
            (self.t2i_width_cm, "pages/t2i/width_cm"),
            (self.t2i_height_cm, "pages/t2i/height_cm"),
            (self.t2i_dpi, "pages/t2i/dpi"),
            (self.t2i_base_url, "pages/t2i/base_url"),
        ):
            value = settings.value(key)
            if value is not None:
                edit.setText(str(value))
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
