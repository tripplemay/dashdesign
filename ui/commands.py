"""Pure command builders for the desktop workflows.

Each builder takes an immutable form snapshot (plain data, no Qt) and returns
``(command, output_dir, env)`` ready for QProcess, raising ``ValueError`` with
a user-facing message when validation fails. Keeping this Qt-free makes the
validation and CLI-assembly logic unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app_runtime import (
    baseline_path,
    prompt_template_library_path,
    runtime_model_dir,
    runtime_tool_path,
    worker_prefix,
)

def api_env(base_url: str, api_key: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if base_url.strip():
        env["OPENAI_BASE_URL"] = base_url.strip()
    if api_key.strip():
        env["OPENAI_API_KEY"] = api_key.strip()
    return env


@dataclass(frozen=True)
class TextImageForm:
    output_dir: str
    prompt: str
    mode: str
    poster_copy: str
    width_cm: str
    height_cm: str
    dpi: str
    candidates: str
    full_style: str
    text_style: str
    purpose_template: str
    style_template: str
    layout_template: str
    text_density: str
    image_size: str
    quality: str
    execute: bool
    postprocess: bool
    base_url: str
    api_key: str


def build_text_image_command(form: TextImageForm):
    output_dir = Path(form.output_dir).expanduser()
    prompt = form.prompt.strip()
    mode = form.mode or "background"
    if not prompt and mode != "full_poster":
        raise ValueError("请填写文生图提示词")
    poster_copy = form.poster_copy.strip()
    if mode in {"poster", "full_poster"} and not poster_copy:
        raise ValueError("带文字海报或完整海报模式需要填写海报文案")
    try:
        width_cm = float(form.width_cm.strip())
        height_cm = float(form.height_cm.strip())
        dpi = int(form.dpi.strip())
    except ValueError as exc:
        raise ValueError("宽、高和 DPI 必须是数字") from exc
    if width_cm <= 0 or height_cm <= 0 or dpi <= 0:
        raise ValueError("宽、高和 DPI 必须大于 0")
    try:
        candidates = int(form.candidates.strip() or "1")
    except ValueError as exc:
        raise ValueError("候选数必须是整数") from exc
    if candidates <= 0:
        raise ValueError("候选数必须大于 0")
    full_style = form.full_style.strip()

    worker_name = "full-poster" if mode == "full_poster" else "text-image"
    command = [
        *worker_prefix(),
        worker_name,
        "--baseline",
        str(baseline_path()),
        "--output-dir",
        str(output_dir),
        "--width-cm",
        str(width_cm),
        "--height-cm",
        str(height_cm),
        "--dpi",
        str(dpi),
        "--prompt",
        prompt,
        "--image-size",
        form.image_size,
        "--quality",
        form.quality,
    ]
    if mode == "full_poster":
        command += [
            "--template-library",
            str(prompt_template_library_path()),
            "--purpose-template",
            form.purpose_template or "course_enrollment",
            "--style-template",
            form.style_template or "tech_neon",
            "--layout-template",
            form.layout_template or "headline_modules_cta",
            "--text-density",
            form.text_density or "medium",
            "--negative-template",
            "full_poster",
            "--style",
            full_style,
            "--candidates",
            str(candidates),
        ]
    else:
        command += [
            "--mode",
            mode,
            "--text-style",
            form.text_style or "clean_edu",
        ]
    if poster_copy:
        command += ["--poster-copy", poster_copy]
    if form.execute:
        command.append("--execute")
    if form.postprocess:
        command.append("--postprocess-print")

    return command, output_dir, api_env(form.base_url, form.api_key)


@dataclass(frozen=True)
class BatchForm:
    input_dir: str
    output_dir: str
    style_mode: bool
    dpi: str
    only: str
    force: bool
    keep_masters: bool


def build_batch_command(form: BatchForm):
    if not form.input_dir.strip():
        raise ValueError("请选择输入目录")
    input_dir = Path(form.input_dir).expanduser()
    output_dir = Path(form.output_dir).expanduser()
    if not input_dir.exists():
        raise ValueError("输入目录不存在")
    dpi = form.dpi.strip()
    if not dpi:
        raise ValueError("请填写 DPI")
    command = worker_prefix()
    if form.style_mode:
        command += [
            "batch-style",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--dpi",
            dpi,
            "--realesrgan-binary",
            str(runtime_tool_path()),
            "--realesrgan-model-dir",
            str(runtime_model_dir()),
        ]
        if form.force:
            command.append("--force")
        if form.keep_masters:
            command.append("--keep-masters")
    else:
        command += [
            "batch-pil",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--dpi",
            dpi,
        ]
    only = form.only.strip()
    if only:
        command += ["--only", only]
    return command, output_dir, {}


@dataclass(frozen=True)
class GptForm:
    source: str
    output_dir: str
    mode: str
    dpi: str
    description: str
    execute: bool
    base_url: str
    api_key: str


def build_gpt_command(form: GptForm):
    source = Path(form.source).expanduser()
    output_dir = Path(form.output_dir).expanduser()
    if not source.exists():
        raise ValueError("源图片不存在")
    command = [
        *worker_prefix(),
        "gpt",
        str(source),
        "--output-dir",
        str(output_dir),
        "--print-dpi",
        form.dpi.strip(),
        "--api-mode",
        form.mode,
    ]
    description = form.description.strip()
    if description:
        command += ["--description", description]
    if form.execute:
        command.append("--execute")
    return command, output_dir, api_env(form.base_url, form.api_key)


@dataclass(frozen=True)
class QrForm:
    source: str
    output_dir: str
    box: str
    reference_size: str
    margin: str
    radius: str


_BOX_PATTERN = re.compile(r"\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*")
_SIZE_PATTERN = re.compile(r"\s*\d+\s*x\s*\d+\s*", re.IGNORECASE)


def build_qr_command(form: QrForm):
    source = Path(form.source).expanduser()
    output_dir = Path(form.output_dir).expanduser()
    if not source.exists():
        raise ValueError("输入图片不存在")
    box = form.box.strip()
    if not box:
        raise ValueError("请填写清除区域 x1,y1,x2,y2（可在预览图上框选）")
    match = _BOX_PATTERN.fullmatch(box)
    if match is None:
        raise ValueError("清除区域格式应为四个非负整数：x1,y1,x2,y2")
    x1, y1, x2, y2 = (int(value) for value in match.groups())
    if x2 <= x1 or y2 <= y1:
        raise ValueError("清除区域无效：需要 x2 > x1 且 y2 > y1")
    box = f"{x1},{y1},{x2},{y2}"
    if form.reference_size.strip() and _SIZE_PATTERN.fullmatch(form.reference_size) is None:
        raise ValueError("参考尺寸格式应为 宽x高，如 3238x1295")
    command = [
        *worker_prefix(),
        "qr",
        str(source),
        "--output-dir",
        str(output_dir),
        "--box",
        box,
        "--margin-ratio",
        form.margin.strip(),
        "--inpaint-radius",
        form.radius.strip(),
    ]
    reference_size = form.reference_size.strip()
    if reference_size:
        command += ["--reference-size", reference_size]
    return command, output_dir, {}
