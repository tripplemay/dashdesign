"""Batch print output page."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app_runtime import first_image, runtime_tool_path
from ui.output_paths import default_output, restore_output
from ui.commands import BatchForm
from ui.utils import scrollable_page_layout
from ui.widgets import PathField

# 与 scripts/prepare_print_assets.py 的 discover_sources 保持一致：
# 只处理这三种扩展名，且文件名必须含 "NNN乘以NNN" 物理尺寸，或同目录有
# print_spec.json（文生图/整幅海报产包的 master.png 走此回退）；
# --only 支持精确文件名或 shell 通配符（fnmatch）。
# _SIZE_RE 与 _dir_print_spec_size 必须与 prepare_print_assets 的 SIZE_RE /
# size_from_print_spec 语义一致（两处独立定义，改一处务必同步另一处）。
# 正则接受 乘/乘以/x/×/* 分隔符；刻意不收 -、_ 以免误吃日期。
_SCRIPT_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
_SIZE_RE = re.compile(r"(\d+)\s*(?:乘以|乘|[xX*×])\s*(\d+)")


def _dir_print_spec_size(directory: Path) -> "tuple[int, int] | None":
    """同目录 print_spec.json 的物理尺寸；缺失/不可用返回 None。"""
    try:
        spec = json.loads((directory / "print_spec.json").read_text(encoding="utf-8"))
        width_cm = float(spec["width_cm"])
        height_cm = float(spec["height_cm"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if width_cm <= 0 or height_cm <= 0:
        return None
    return int(round(width_cm)), int(round(height_cm))


class BatchPage(QWidget):
    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        layout = scrollable_page_layout(self)

        paths = QGroupBox("输入输出")
        path_layout = QVBoxLayout(paths)
        self.batch_input = PathField(
            "输入目录",
            "",
            "dir",
            placeholder="选择包含源图片的目录（文件名需含尺寸，如 200乘以80）",
        )
        self.batch_output = PathField("输出目录", default_output("print_ready_desktop_qt"), "dir")
        path_layout.addWidget(self.batch_input)
        path_layout.addWidget(self.batch_output)
        hint = QLabel(
            "仅处理 jpg/jpeg/png；物理尺寸从文件名解析（如“200乘以80”或“200x80”= 200cm×80cm），请勿改名。"
            "文生图产物目录（含 print_spec.json）可直接选择，master.png 无需改名。"
        )
        hint.setObjectName("Subtitle")
        hint.setWordWrap(True)
        path_layout.addWidget(hint)
        layout.addWidget(paths)

        workflow = QGroupBox("处理方式")
        workflow_layout = QVBoxLayout(workflow)
        self.workflow_group = QButtonGroup(self)
        self.style_radio = QRadioButton("保留原字效高清化（Real-ESRGAN，推荐）")
        self.style_radio.setToolTip(
            "先用 Real-ESRGAN x4 超分再归一化到目标尺寸，字效和插画边缘最好；\n"
            "源图较大（>5M 像素或最长边 >4300 或 DPI≥160）时自动回退基础输出。速度较慢。"
        )
        self.pil_radio = QRadioButton("基础 200dpi 输出（PIL/Lanczos，稳定兜底）")
        self.pil_radio.setToolTip("纯 PIL/Lanczos 缩放，速度快、结果稳定；适合已经足够清晰的源图。")
        self.style_radio.setChecked(True)
        self.workflow_group.addButton(self.style_radio)
        self.workflow_group.addButton(self.pil_radio)
        workflow_layout.addWidget(self.style_radio)
        workflow_layout.addWidget(self.pil_radio)
        self.tool_status = QLabel("")
        self.tool_status.setObjectName("Subtitle")
        self.tool_status.setWordWrap(True)
        workflow_layout.addWidget(self.tool_status)
        self._refresh_tool_status()
        layout.addWidget(workflow)

        options = QGroupBox("参数")
        option_layout = QGridLayout(options)
        self.batch_dpi = QSpinBox()
        self.batch_dpi.setRange(30, 600)
        self.batch_dpi.setValue(200)
        self.batch_dpi.setToolTip("印刷输出分辨率：写真/展架常用 200，大幅喷绘可用 150。")
        self.batch_only = QLineEdit()
        self.batch_only.setPlaceholderText("可选：只处理某个文件名，支持通配符（如 海报*.jpg）")
        self.batch_force = QCheckBox("覆盖/强制重新生成")
        self.batch_force.setToolTip("勾选后即使输出目录已有同名成品也会重新生成并覆盖。")
        self.batch_keep_masters = QCheckBox("保留 Real-ESRGAN 中间 master")
        self.batch_keep_masters.setToolTip("保留超分后的中间 PNG（_masters/ 目录），便于排查画质问题，占用较多磁盘。")
        option_layout.addWidget(QLabel("DPI"), 0, 0)
        option_layout.addWidget(self.batch_dpi, 0, 1)
        option_layout.addWidget(QLabel("只处理文件"), 1, 0)
        option_layout.addWidget(self.batch_only, 1, 1)
        option_layout.addWidget(QLabel("选项"), 2, 0)
        option_layout.addWidget(self.batch_force, 2, 1)
        option_layout.addWidget(self.batch_keep_masters, 3, 1)
        option_layout.setColumnStretch(1, 1)
        layout.addWidget(options)
        layout.addStretch(1)

    def _refresh_tool_status(self) -> None:
        if runtime_tool_path().exists():
            self.tool_status.setText("")
            self.tool_status.hide()
        else:
            self.tool_status.setText(
                "⚠ 未找到 Real-ESRGAN 二进制（tools/realesrgan-ncnn-vulkan）。"
                "请先运行 scripts/bootstrap_runtime_assets.sh，或改用基础输出。"
            )
            self.tool_status.show()

    def _count_images(self) -> "tuple[int, int]":
        """返回 (可处理数, 因文件名不含尺寸被跳过数)，复刻 discover_sources 语义。"""
        input_dir = Path(self.batch_input.text()).expanduser()
        only = self.batch_only.text().strip()
        if not input_dir.is_dir():
            return 0, 0
        processable = 0
        skipped_no_size = 0
        spec_size = _dir_print_spec_size(input_dir)
        for path in input_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in _SCRIPT_IMAGE_EXTENSIONS:
                continue
            if only and not (path.name == only or fnmatch.fnmatch(path.name, only)):
                continue
            if _SIZE_RE.search(path.name) or spec_size is not None:
                processable += 1
            else:
                skipped_no_size += 1
        return processable, skipped_no_size

    def confirm_run(self, window) -> bool:  # type: ignore[no-untyped-def]
        self._refresh_tool_status()
        if self.style_radio.isChecked() and not runtime_tool_path().exists():
            window.banner.show_message(
                "error",
                "未找到 Real-ESRGAN 二进制，无法使用“保留原字效高清化”。"
                "请先运行 scripts/bootstrap_runtime_assets.sh，或切换为基础输出。",
            )
            return False
        count, skipped = self._count_images()
        if count == 0:
            window.banner.show_message(
                "error",
                "输入目录中没有可处理的图片：仅支持 jpg/jpeg/png，"
                "且文件名必须含物理尺寸（如“200乘以80”或“200x80”），"
                "或选择文生图产物目录（同目录含 print_spec.json）。"
                + (f" 有 {skipped} 张图片因文件名不含尺寸被跳过。" if skipped else ""),
            )
            return False
        message = f"将处理 {count} 张图片，输出到：\n{self.batch_output.text()}"
        if skipped:
            message += f"\n\n另有 {skipped} 张图片文件名不含尺寸（如“200乘以80”），将被跳过。"
        if self.batch_force.isChecked():
            message += "\n\n已勾选“覆盖/强制重新生成”，已有输出将被覆盖。"
        reply = QMessageBox.question(
            window,
            "确认批量处理",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        return reply == QMessageBox.StandardButton.Yes

    def form(self) -> BatchForm:
        return BatchForm(
            input_dir=self.batch_input.text(),
            output_dir=self.batch_output.text(),
            style_mode=self.style_radio.isChecked(),
            dpi=str(self.batch_dpi.value()),
            only=self.batch_only.text(),
            force=self.batch_force.isChecked(),
            keep_masters=self.batch_keep_masters.isChecked(),
        )

    def input_preview_path(self) -> "Path | None":
        if not self.batch_input.text():
            return None
        input_dir = Path(self.batch_input.text()).expanduser()
        only = self.batch_only.text().strip()
        if only:
            path = input_dir / only
            return path if path.exists() else None
        return first_image(input_dir)

    def save_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.setValue("pages/batch/input_dir", self.batch_input.text())
        settings.setValue("pages/batch/output_dir", self.batch_output.text())
        settings.setValue("pages/batch/style_mode", self.style_radio.isChecked())
        settings.setValue("pages/batch/dpi", self.batch_dpi.value())
        settings.setValue("pages/batch/force", self.batch_force.isChecked())
        settings.setValue("pages/batch/keep_masters", self.batch_keep_masters.isChecked())

    def restore_settings(self, settings) -> None:  # type: ignore[no-untyped-def]
        self.batch_input.setText(str(settings.value("pages/batch/input_dir", self.batch_input.text())))
        self.batch_output.setText(
            restore_output(str(settings.value("pages/batch/output_dir", "")), "print_ready_desktop_qt")
        )
        if settings.value("pages/batch/style_mode", True, type=bool):
            self.style_radio.setChecked(True)
        else:
            self.pil_radio.setChecked(True)
        self.batch_dpi.setValue(settings.value("pages/batch/dpi", self.batch_dpi.value(), type=int))
        self.batch_force.setChecked(settings.value("pages/batch/force", False, type=bool))
        self.batch_keep_masters.setChecked(
            settings.value("pages/batch/keep_masters", False, type=bool)
        )