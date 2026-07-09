#!/usr/bin/env python3
"""Generate baseline-aware text-to-image packages for print poster production."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import textwrap
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import progress

from image_api_client import execute_image_generation
from prepare_print_assets import (
    SR_MAX_INPUT_EDGE,
    SR_MAX_INPUT_PIXELS,
    aspect_delta_percent,
    enhance,
    fit_with_blurred_background,
    run_realesrgan,
    save_print_image,
    target_pixels,
)


PROMPT_TEMPLATE_VERSION = "text_to_image_print.v3"
DEFAULT_BASELINE = Path("docs") / "baseline" / "baseline.v1.draft.json"
DEFAULT_OUTPUT_DIR = Path("workflow_samples") / "text_to_image_print"
IMAGE_SIZE_RE = re.compile(r"^\d+x\d+$")
MODE_BACKGROUND = "background"
MODE_POSTER = "poster"
MODE_CHOICES = (MODE_BACKGROUND, MODE_POSTER)
TEXT_STYLE_CLEAN_EDU = "clean_edu"
TEXT_STYLE_TECH_NEON = "tech_neon"
TEXT_STYLE_CHOICES = (TEXT_STYLE_CLEAN_EDU, TEXT_STYLE_TECH_NEON)
TEXT_STYLE_NAMES = {
    TEXT_STYLE_CLEAN_EDU: "clean readable education poster typography",
    TEXT_STYLE_TECH_NEON: "AI tech neon poster typography",
}
PROTECTED_CJK_PHRASES = (
    "AI课程",
    "AI绘图",
    "AI视频",
    "AI漫剧",
    "AI编程",
    "小程序",
    "孩子",
    "学习",
    "未来",
    "课程",
    "免费",
    "预约",
    "福利",
    "绘图",
    "编程",
    "网页",
    "创意",
)
TEXT_TOKEN_RE = re.compile(
    "|".join(re.escape(item) for item in PROTECTED_CJK_PHRASES)
    + r"|[A-Za-z0-9][A-Za-z0-9_+\-./#&%]*|[\u4e00-\u9fff]|[^\s]"
)
NO_BREAK_BEFORE = set("，。！？；：、,.!?;:)]}）》」』”’")
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/PingFang.ttc"),
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
]
SUSPICIOUS_COPY_PATTERNS = [
    (
        re.compile(r"(?<![A-Za-z])Al(?=[\u4e00-\u9fff])"),
        "疑似把英文大写 AI 写成了 Al，请确认海报文案。",
    ),
    (
        re.compile(r"画画作|故事动活|免得AI"),
        "检测到疑似错别字，请在输出前复核海报文案。",
    ),
]


@dataclass(frozen=True)
class PosterCopy:
    headline: str = ""
    subtitle: str = ""
    modules: list[str] | None = None
    cta: str = ""
    raw_text: str = ""

    def normalized_modules(self) -> list[str]:
        return [item.strip() for item in (self.modules or []) if item.strip()]

    def has_content(self) -> bool:
        return bool(
            self.headline.strip()
            or self.subtitle.strip()
            or self.cta.strip()
            or self.normalized_modules()
        )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def cm_label(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return str(number).rstrip("0").rstrip(".").replace(".", "p")


def evidenced_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("text", "")).strip()
    return str(value or "").strip()


def text_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    output: list[str] = []
    for value in values:
        text = evidenced_text(value)
        if text:
            output.append(text)
    return output


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Baseline file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Baseline JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Baseline JSON root must be an object")
    return payload


def prompt_context_source(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline_id": baseline.get("baseline_id"),
        "version": baseline.get("version"),
        "target_audience_mode": baseline.get("target_audience_mode"),
        "project": {
            "name": baseline.get("project", {}).get("name", ""),
            "category": baseline.get("project", {}).get("category", []),
        },
        "consumer_baseline": baseline.get("consumer_baseline", {}),
        "visual_guidelines": baseline.get("visual_guidelines", {}),
        "prompt_policy": baseline.get("prompt_policy", {}),
    }


def profile_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def resolve_image_size(width_cm: float, height_cm: float, requested: str) -> str:
    requested = requested.strip().lower()
    if requested and requested != "auto":
        if not IMAGE_SIZE_RE.match(requested):
            raise ValueError("Image size must be auto or WIDTHxHEIGHT, such as 1536x1024")
        return requested

    ratio = width_cm / height_cm
    if ratio > 1.2:
        return "1536x1024"
    if ratio < 0.8:
        return "1024x1536"
    return "1024x1024"


def output_suffix(output_format: str) -> str:
    value = output_format.strip().lower()
    if value == "jpeg":
        return "jpg"
    return value or "png"


def blocked_terms_in_prompt(user_prompt: str, baseline: dict[str, Any]) -> list[str]:
    consumer = baseline.get("consumer_baseline", {})
    blocked = consumer.get("blocked_keywords", []) if isinstance(consumer, dict) else []
    return [str(term) for term in blocked if str(term) and str(term) in user_prompt]


def parse_poster_copy(
    raw_text: str,
    headline: str | None = None,
    subtitle: str | None = None,
    modules: list[str] | None = None,
    cta: str | None = None,
) -> PosterCopy:
    parsed_headline = (headline or "").strip()
    parsed_subtitle = (subtitle or "").strip()
    parsed_modules = [item.strip() for item in (modules or []) if item.strip()]
    parsed_cta = (cta or "").strip()
    current_section = ""
    fallback_lines: list[str] = []

    for raw_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip().strip("-•")
        if not line:
            continue
        normalized = re.sub(r"\s+", "", line)
        key_value = re.match(r"^([^:：]{2,12})[:：](.+)$", line)
        if key_value:
            key = key_value.group(1).strip()
            value = key_value.group(2).strip()
            if key in {"主标题", "标题", "大标题"} and not parsed_headline:
                parsed_headline = value
                current_section = ""
                continue
            if key in {"副标题", "副标", "说明"} and not parsed_subtitle:
                parsed_subtitle = value
                current_section = ""
                continue
            if key in {"结语", "行动语", "行动号召", "CTA", "福利", "报名"} and not parsed_cta:
                parsed_cta = value
                current_section = ""
                continue
            if key in {"课程类型", "课程模块", "模块", "课程"}:
                current_section = "modules"
                if value:
                    parsed_modules.append(value)
                continue
            if key.upper().startswith("AI") or key in {"AI绘图", "AI视频", "AI漫剧", "AI编程"}:
                parsed_modules.append(f"{key}：{value}")
                current_section = "modules"
                continue

        if normalized in {"课程类型", "课程模块", "模块", "课程"}:
            current_section = "modules"
            continue
        if current_section == "modules" or normalized.upper().startswith("AI"):
            parsed_modules.append(line)
            continue
        fallback_lines.append(line)

    if not parsed_headline and fallback_lines:
        parsed_headline = fallback_lines.pop(0)
    if not parsed_cta and len(fallback_lines) >= 2:
        parsed_cta = fallback_lines.pop()
    if not parsed_subtitle and fallback_lines:
        parsed_subtitle = " ".join(fallback_lines)

    return PosterCopy(
        headline=parsed_headline,
        subtitle=parsed_subtitle,
        modules=parsed_modules,
        cta=parsed_cta,
        raw_text=raw_text.strip(),
    )


def find_font_path() -> Path | None:
    for path in FONT_CANDIDATES:
        if path.exists():
            return path
    return None


def load_font(font_path: Path | None, size: int) -> ImageFont.ImageFont:
    if font_path:
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, stroke_width: int = 0) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def text_tokens(text: str) -> list[str]:
    return TEXT_TOKEN_RE.findall(text)


def wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    stroke_width: int = 0,
) -> list[str]:
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    lines: list[str] = []
    for paragraph in paragraphs:
        current = ""
        for token in text_tokens(paragraph):
            candidate = current + token
            width, _ = text_size(draw, candidate, font, stroke_width)
            if current and width > max_width:
                if token in NO_BREAK_BEFORE:
                    current = candidate
                    continue
                lines.append(current)
                token_width, _ = text_size(draw, token, font, stroke_width)
                if token_width <= max_width:
                    current = token
                else:
                    current = ""
                    for char in token:
                        char_candidate = current + char
                        char_width, _ = text_size(draw, char_candidate, font, stroke_width)
                        if current and char_width > max_width:
                            lines.append(current)
                            current = char
                        else:
                            current = char_candidate
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines or [""]


def fit_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path | None,
    start_size: int,
    min_size: int,
    max_width: int,
    max_height: int,
    stroke_width: int = 0,
) -> tuple[ImageFont.ImageFont, list[str], int]:
    for size in range(start_size, min_size - 1, -2):
        font = load_font(font_path, size)
        lines = wrap_text(draw, text, font, max_width, stroke_width)
        line_height = max(1, text_size(draw, "国", font, stroke_width)[1])
        spacing = max(4, int(size * 0.18))
        total_height = len(lines) * line_height + max(0, len(lines) - 1) * spacing
        has_short_tail = (
            len(lines) > 1
            and len(lines[-1]) <= 2
            and len("".join(lines[:-1])) >= 8
            and size > min_size
        )
        if total_height <= max_height and not has_short_tail:
            return font, lines, spacing
    font = load_font(font_path, min_size)
    return font, wrap_text(draw, text, font, max_width, stroke_width), max(3, int(min_size * 0.15))


def draw_text_block(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    lines: list[str],
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    spacing: int,
    stroke_fill: tuple[int, int, int] | None = None,
    stroke_width: int = 0,
    align: str = "left",
    max_width: int | None = None,
) -> int:
    x, y = xy
    for line in lines:
        line_x = x
        if max_width is not None and align != "left":
            line_width, _ = text_size(draw, line, font, stroke_width)
            if align == "center":
                line_x = x + max(0, max_width - line_width) // 2
            elif align == "right":
                line_x = x + max(0, max_width - line_width)
        draw.text(
            (line_x, y),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        _, line_height = text_size(draw, line or "国", font, stroke_width)
        y += line_height + spacing
    return y


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    stroke_fill: tuple[int, int, int] | None = None,
    stroke_width: int = 0,
) -> None:
    x1, y1, x2, y2 = box
    width, height = text_size(draw, text, font, stroke_width)
    draw.text(
        (x1 + (x2 - x1 - width) // 2, y1 + (y2 - y1 - height) // 2),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def text_block_size(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    spacing: int,
    stroke_width: int = 0,
) -> tuple[int, int]:
    line_sizes = [text_size(draw, line or "国", font, stroke_width) for line in lines]
    width = max((item[0] for item in line_sizes), default=0)
    height = sum(item[1] for item in line_sizes) + max(0, len(line_sizes) - 1) * spacing
    return width, height


def split_module_copy(module: str) -> tuple[str, str]:
    if "：" in module:
        title, body = module.split("：", 1)
    elif ":" in module:
        title, body = module.split(":", 1)
    else:
        title, body = module, ""
    return title.strip(), body.strip()


def has_qr_request(poster_copy: PosterCopy) -> bool:
    return "扫码" in poster_copy.raw_text or "二维码" in poster_copy.raw_text


def analyze_poster_copy(poster_copy: PosterCopy) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    text = "\n".join(
        [
            poster_copy.raw_text,
            poster_copy.headline,
            poster_copy.subtitle,
            "\n".join(poster_copy.normalized_modules()),
            poster_copy.cta,
        ]
    )
    for pattern, message in SUSPICIOUS_COPY_PATTERNS:
        if pattern.search(text):
            warnings.append({"type": "copy_suspicion", "message": message})
    if has_qr_request(poster_copy):
        warnings.append(
            {
                "type": "qr_placeholder",
                "message": "本地合成只绘制二维码占位框，不生成可扫描二维码。",
            }
        )
    return warnings


def draw_glow_text_block(
    image: Image.Image,
    xy: tuple[int, int],
    lines: list[str],
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    spacing: int,
    glow_fill: tuple[int, int, int],
    glow_radius: int,
    stroke_fill: tuple[int, int, int] | None = None,
    stroke_width: int = 0,
    align: str = "left",
    max_width: int | None = None,
) -> int:
    glow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    draw_text_block(
        glow_draw,
        xy,
        lines,
        font,
        (*glow_fill, 220),
        spacing,
        stroke_fill=(*glow_fill, 220),
        stroke_width=max(stroke_width + 2, 2),
        align=align,
        max_width=max_width,
    )
    image.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(glow_radius)))
    draw = ImageDraw.Draw(image)
    return draw_text_block(
        draw,
        xy,
        lines,
        font,
        fill,
        spacing,
        stroke_fill=stroke_fill,
        stroke_width=stroke_width,
        align=align,
        max_width=max_width,
    )


def draw_glow_rectangle(
    overlay: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    outline: tuple[int, int, int],
    width: int,
    blur: int,
) -> None:
    glow_layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_draw.rounded_rectangle(
        box,
        radius=radius,
        outline=(*outline, 190),
        width=max(width * 2, width + 1),
    )
    overlay.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(blur)))


def compose_clean_edu_layout(
    image: Image.Image,
    overlay: Image.Image,
    poster_copy: PosterCopy,
    font_path: Path | None,
) -> dict[str, Any]:
    width, height = image.size
    landscape = width >= height
    draw_image = ImageDraw.Draw(image)
    draw_overlay = ImageDraw.Draw(overlay)
    margin = int(width * (0.055 if landscape else 0.06))
    white = (255, 255, 255)
    ink = (20, 29, 56)
    body = (36, 45, 72)
    accent = (0, 147, 178)
    warm_accent = (255, 179, 72)

    title_max_w = int(width * (0.82 if landscape else 0.86))
    title_x = (width - title_max_w) // 2
    title_y = int(height * (0.055 if landscape else 0.045))
    title_font, title_lines, title_spacing = fit_text_block(
        draw_image,
        poster_copy.headline,
        font_path,
        int(height * (0.072 if landscape else 0.047)),
        max(24, int(height * 0.023)),
        title_max_w,
        int(height * (0.16 if landscape else 0.13)),
        stroke_width=max(2, int(height * 0.0025)),
    )
    shadow_offset = max(2, int(height * 0.004))
    draw_text_block(
        draw_overlay,
        (title_x + shadow_offset, title_y + shadow_offset),
        title_lines,
        title_font,
        (255, 255, 255, 150),
        title_spacing,
        stroke_fill=(255, 255, 255, 150),
        stroke_width=max(2, int(height * 0.003)),
        align="center",
        max_width=title_max_w,
    )
    cursor_y = draw_text_block(
        draw_overlay,
        (title_x, title_y),
        title_lines,
        title_font,
        ink,
        title_spacing,
        stroke_fill=white,
        stroke_width=max(2, int(height * 0.0025)),
        align="center",
        max_width=title_max_w,
    )

    if poster_copy.subtitle:
        subtitle_max_w = int(width * (0.72 if landscape else 0.84))
        subtitle_x = (width - subtitle_max_w) // 2
        subtitle_font, subtitle_lines, subtitle_spacing = fit_text_block(
            draw_overlay,
            poster_copy.subtitle,
            font_path,
            int(height * (0.029 if landscape else 0.021)),
            max(16, int(height * 0.014)),
            subtitle_max_w,
            int(height * 0.095),
            stroke_width=max(1, int(height * 0.0012)),
        )
        draw_text_block(
            draw_overlay,
            (subtitle_x, cursor_y + int(height * 0.018)),
            subtitle_lines,
            subtitle_font,
            body,
            subtitle_spacing,
            stroke_fill=white,
            stroke_width=max(1, int(height * 0.0012)),
            align="center",
            max_width=subtitle_max_w,
        )

    modules = poster_copy.normalized_modules()
    if modules:
        gap = max(14, int(width * 0.014))
        cols = min(4, len(modules)) if landscape else min(2, len(modules))
        rows = (len(modules) + cols - 1) // cols
        grid_x = margin
        grid_w = width - margin * 2
        card_w = (grid_w - gap * (cols - 1)) // cols
        card_h = int(height * (0.155 if landscape else 0.118))
        total_h = rows * card_h + max(0, rows - 1) * gap
        grid_y = int(height * (0.54 if landscape else 0.57))
        if grid_y + total_h > int(height * 0.84):
            grid_y = max(int(height * 0.45), int(height * 0.84) - total_h)
        for index, module in enumerate(modules[: cols * rows]):
            col = index % cols
            row = index // cols
            x1 = grid_x + col * (card_w + gap)
            y1 = grid_y + row * (card_h + gap)
            x2 = x1 + card_w
            y2 = y1 + card_h
            radius = max(10, int(height * 0.014))
            draw_overlay.rounded_rectangle(
                (x1 + 5, y1 + 6, x2 + 5, y2 + 6),
                radius=radius,
                fill=(14, 24, 48, 48),
            )
            draw_overlay.rounded_rectangle(
                (x1, y1, x2, y2),
                radius=radius,
                fill=(255, 255, 255, 218),
                outline=(108, 191, 206, 220),
                width=max(2, int(height * 0.002)),
            )
            draw_overlay.rounded_rectangle(
                (x1, y1, x2, y1 + max(5, int(card_h * 0.055))),
                radius=radius,
                fill=(*accent, 235),
            )
            title, detail = split_module_copy(module)
            inner_x = x1 + int(card_w * 0.08)
            inner_w = card_w - int(card_w * 0.16)
            module_title_font, module_title_lines, module_title_spacing = fit_text_block(
                draw_overlay,
                title,
                font_path,
                int(height * (0.027 if landscape else 0.019)),
                max(13, int(height * 0.012)),
                inner_w,
                int(card_h * 0.35),
            )
            module_y = y1 + int(card_h * 0.18)
            module_y = draw_text_block(
                draw_overlay,
                (inner_x, module_y),
                module_title_lines,
                module_title_font,
                ink,
                module_title_spacing,
            )
            if detail:
                detail_font, detail_lines, detail_spacing = fit_text_block(
                    draw_overlay,
                    detail,
                    font_path,
                    int(height * (0.019 if landscape else 0.014)),
                    max(10, int(height * 0.009)),
                    inner_w,
                    max(1, y2 - module_y - int(card_h * 0.12)),
                )
                draw_text_block(
                    draw_overlay,
                    (inner_x, module_y + int(card_h * 0.08)),
                    detail_lines,
                    detail_font,
                    body,
                    detail_spacing,
                )

    if poster_copy.cta:
        qr_needed = has_qr_request(poster_copy)
        qr_size = int(height * (0.13 if landscape else 0.078)) if qr_needed else 0
        gap = max(16, int(width * 0.018))
        cta_h = int(height * (0.088 if landscape else 0.064))
        cta_y1 = height - margin - cta_h
        cta_x1 = margin
        cta_x2 = width - margin - (qr_size + gap if qr_needed else 0)
        if cta_x2 <= cta_x1:
            cta_x2 = width - margin
            qr_size = 0
        cta_box = (cta_x1, cta_y1, cta_x2, cta_y1 + cta_h)
        draw_overlay.rounded_rectangle(
            (cta_box[0] + 5, cta_box[1] + 7, cta_box[2] + 5, cta_box[3] + 7),
            radius=max(12, int(cta_h * 0.25)),
            fill=(8, 18, 42, 72),
        )
        draw_overlay.rounded_rectangle(
            cta_box,
            radius=max(12, int(cta_h * 0.25)),
            fill=(13, 31, 62, 238),
            outline=(*warm_accent, 230),
            width=max(2, int(height * 0.002)),
        )
        cta_font, cta_lines, cta_spacing = fit_text_block(
            draw_overlay,
            poster_copy.cta,
            font_path,
            int(cta_h * 0.38),
            max(14, int(cta_h * 0.2)),
            int((cta_x2 - cta_x1) * 0.86),
            int(cta_h * 0.72),
        )
        _, cta_text_h = text_block_size(draw_overlay, cta_lines, cta_font, cta_spacing)
        draw_text_block(
            draw_overlay,
            (cta_x1 + int((cta_x2 - cta_x1) * 0.07), cta_y1 + (cta_h - cta_text_h) // 2),
            cta_lines,
            cta_font,
            white,
            cta_spacing,
        )

        if qr_needed and qr_size > 0:
            qr_x2 = width - margin
            qr_y2 = cta_y1 + cta_h
            qr_box = (qr_x2 - qr_size, qr_y2 - qr_size, qr_x2, qr_y2)
            draw_overlay.rounded_rectangle(
                qr_box,
                radius=max(8, int(qr_size * 0.08)),
                fill=(255, 255, 255, 238),
                outline=(13, 31, 62, 220),
                width=max(2, int(qr_size * 0.024)),
            )
            qr_font = load_font(font_path, max(12, int(qr_size * 0.12)))
            draw_centered_text(draw_overlay, qr_box, "二维码预留", qr_font, body)

    return {"layout_template": TEXT_STYLE_CLEAN_EDU}


def compose_tech_neon_layout(
    image: Image.Image,
    overlay: Image.Image,
    poster_copy: PosterCopy,
    font_path: Path | None,
) -> dict[str, Any]:
    width, height = image.size
    landscape = width >= height
    draw_image = ImageDraw.Draw(image)
    draw_overlay = ImageDraw.Draw(overlay)
    margin = int(width * (0.052 if landscape else 0.058))
    cyan = (78, 230, 255)
    magenta = (255, 84, 230)
    purple = (130, 96, 255)
    white = (255, 255, 255)
    deep = (5, 12, 34)

    title_max_w = int(width * (0.84 if landscape else 0.88))
    title_x = (width - title_max_w) // 2
    title_y = int(height * (0.05 if landscape else 0.042))
    title_font, title_lines, title_spacing = fit_text_block(
        draw_image,
        poster_copy.headline,
        font_path,
        int(height * (0.08 if landscape else 0.05)),
        max(24, int(height * 0.024)),
        title_max_w,
        int(height * (0.17 if landscape else 0.14)),
        stroke_width=max(3, int(height * 0.003)),
    )
    cursor_y = draw_glow_text_block(
        image,
        (title_x, title_y),
        title_lines,
        title_font,
        white,
        title_spacing,
        glow_fill=magenta,
        glow_radius=max(5, int(height * 0.008)),
        stroke_fill=cyan,
        stroke_width=max(2, int(height * 0.003)),
        align="center",
        max_width=title_max_w,
    )

    if poster_copy.subtitle:
        subtitle_max_w = int(width * (0.72 if landscape else 0.84))
        subtitle_x = (width - subtitle_max_w) // 2
        subtitle_font, subtitle_lines, subtitle_spacing = fit_text_block(
            draw_overlay,
            poster_copy.subtitle,
            font_path,
            int(height * (0.031 if landscape else 0.022)),
            max(16, int(height * 0.014)),
            subtitle_max_w,
            int(height * 0.1),
            stroke_width=max(2, int(height * 0.002)),
        )
        draw_glow_text_block(
            overlay,
            (subtitle_x, cursor_y + int(height * 0.02)),
            subtitle_lines,
            subtitle_font,
            white,
            subtitle_spacing,
            glow_fill=cyan,
            glow_radius=max(3, int(height * 0.004)),
            stroke_fill=deep,
            stroke_width=max(2, int(height * 0.002)),
            align="center",
            max_width=subtitle_max_w,
        )

    modules = poster_copy.normalized_modules()
    if modules:
        gap = max(14, int(width * 0.014))
        cols = min(4, len(modules)) if landscape else min(2, len(modules))
        rows = (len(modules) + cols - 1) // cols
        grid_x = margin
        grid_w = width - margin * 2
        card_w = (grid_w - gap * (cols - 1)) // cols
        card_h = int(height * (0.15 if landscape else 0.116))
        total_h = rows * card_h + max(0, rows - 1) * gap
        grid_y = int(height * (0.55 if landscape else 0.58))
        if grid_y + total_h > int(height * 0.84):
            grid_y = max(int(height * 0.46), int(height * 0.84) - total_h)
        for index, module in enumerate(modules[: cols * rows]):
            col = index % cols
            row = index // cols
            x1 = grid_x + col * (card_w + gap)
            y1 = grid_y + row * (card_h + gap)
            x2 = x1 + card_w
            y2 = y1 + card_h
            radius = max(10, int(height * 0.014))
            draw_glow_rectangle(
                overlay,
                (x1, y1, x2, y2),
                radius,
                magenta if index % 2 else cyan,
                max(2, int(height * 0.002)),
                max(5, int(height * 0.006)),
            )
            draw_overlay.rounded_rectangle(
                (x1, y1, x2, y2),
                radius=radius,
                fill=(6, 12, 38, 188),
                outline=(*(magenta if index % 2 else cyan), 230),
                width=max(2, int(height * 0.002)),
            )
            draw_overlay.line(
                (x1 + int(card_w * 0.08), y1 + int(card_h * 0.18), x2 - int(card_w * 0.08), y1 + int(card_h * 0.18)),
                fill=(*purple, 210),
                width=max(2, int(height * 0.002)),
            )
            title, detail = split_module_copy(module)
            inner_x = x1 + int(card_w * 0.08)
            inner_w = card_w - int(card_w * 0.16)
            module_title_font, module_title_lines, module_title_spacing = fit_text_block(
                draw_overlay,
                title,
                font_path,
                int(height * (0.026 if landscape else 0.019)),
                max(13, int(height * 0.012)),
                inner_w,
                int(card_h * 0.34),
                stroke_width=max(1, int(height * 0.001)),
            )
            module_y = y1 + int(card_h * 0.24)
            module_y = draw_text_block(
                draw_overlay,
                (inner_x, module_y),
                module_title_lines,
                module_title_font,
                white,
                module_title_spacing,
                stroke_fill=deep,
                stroke_width=max(1, int(height * 0.001)),
            )
            if detail:
                detail_font, detail_lines, detail_spacing = fit_text_block(
                    draw_overlay,
                    detail,
                    font_path,
                    int(height * (0.018 if landscape else 0.014)),
                    max(10, int(height * 0.009)),
                    inner_w,
                    max(1, y2 - module_y - int(card_h * 0.12)),
                    stroke_width=max(1, int(height * 0.001)),
                )
                draw_text_block(
                    draw_overlay,
                    (inner_x, module_y + int(card_h * 0.07)),
                    detail_lines,
                    detail_font,
                    (226, 248, 255),
                    detail_spacing,
                    stroke_fill=deep,
                    stroke_width=max(1, int(height * 0.001)),
                )

    if poster_copy.cta:
        qr_needed = has_qr_request(poster_copy)
        qr_size = int(height * (0.13 if landscape else 0.078)) if qr_needed else 0
        gap = max(16, int(width * 0.018))
        cta_h = int(height * (0.088 if landscape else 0.064))
        cta_y1 = height - margin - cta_h
        cta_x1 = margin
        cta_x2 = width - margin - (qr_size + gap if qr_needed else 0)
        if cta_x2 <= cta_x1:
            cta_x2 = width - margin
            qr_size = 0
        cta_box = (cta_x1, cta_y1, cta_x2, cta_y1 + cta_h)
        draw_glow_rectangle(
            overlay,
            cta_box,
            max(12, int(cta_h * 0.25)),
            magenta,
            max(2, int(height * 0.002)),
            max(6, int(height * 0.007)),
        )
        draw_overlay.rounded_rectangle(
            cta_box,
            radius=max(12, int(cta_h * 0.25)),
            fill=(7, 12, 42, 225),
            outline=(*magenta, 235),
            width=max(2, int(height * 0.002)),
        )
        draw_overlay.line(
            (cta_x1 + int(cta_h * 0.28), cta_y1 + cta_h - 2, cta_x2 - int(cta_h * 0.28), cta_y1 + cta_h - 2),
            fill=(*cyan, 230),
            width=max(2, int(height * 0.002)),
        )
        cta_font, cta_lines, cta_spacing = fit_text_block(
            draw_overlay,
            poster_copy.cta,
            font_path,
            int(cta_h * 0.4),
            max(14, int(cta_h * 0.2)),
            int((cta_x2 - cta_x1) * 0.86),
            int(cta_h * 0.72),
            stroke_width=max(1, int(height * 0.001)),
        )
        _, cta_text_h = text_block_size(draw_overlay, cta_lines, cta_font, cta_spacing, max(1, int(height * 0.001)))
        draw_text_block(
            draw_overlay,
            (cta_x1 + int((cta_x2 - cta_x1) * 0.07), cta_y1 + (cta_h - cta_text_h) // 2),
            cta_lines,
            cta_font,
            white,
            cta_spacing,
            stroke_fill=deep,
            stroke_width=max(1, int(height * 0.001)),
        )

        if qr_needed and qr_size > 0:
            qr_x2 = width - margin
            qr_y2 = cta_y1 + cta_h
            qr_box = (qr_x2 - qr_size, qr_y2 - qr_size, qr_x2, qr_y2)
            draw_glow_rectangle(
                overlay,
                qr_box,
                max(8, int(qr_size * 0.08)),
                cyan,
                max(2, int(qr_size * 0.024)),
                max(4, int(qr_size * 0.05)),
            )
            draw_overlay.rounded_rectangle(
                qr_box,
                radius=max(8, int(qr_size * 0.08)),
                fill=(255, 255, 255, 232),
                outline=(*cyan, 230),
                width=max(2, int(qr_size * 0.024)),
            )
            qr_font = load_font(font_path, max(12, int(qr_size * 0.12)))
            draw_centered_text(draw_overlay, qr_box, "二维码预留", qr_font, deep)

    return {"layout_template": TEXT_STYLE_TECH_NEON}


def compose_poster_copy(
    background_path: Path,
    output_path: Path,
    poster_copy: PosterCopy,
    dpi: int,
    text_style: str,
) -> dict[str, Any]:
    if text_style not in TEXT_STYLE_CHOICES:
        raise ValueError(f"Text style must be one of: {', '.join(TEXT_STYLE_CHOICES)}")

    with Image.open(background_path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGBA")
        icc_profile = raw.info.get("icc_profile")

    width, height = image.size
    font_path = find_font_path()
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))

    modules = poster_copy.normalized_modules()
    if text_style == TEXT_STYLE_TECH_NEON:
        layout_result = compose_tech_neon_layout(image, overlay, poster_copy, font_path)
    else:
        layout_result = compose_clean_edu_layout(image, overlay, poster_copy, font_path)

    composed = Image.alpha_composite(image, overlay).convert("RGB")
    save_print_image(composed, output_path, dpi, icc_profile)
    return {
        "status": "generated",
        "output": str(output_path),
        "output_px": f"{width}x{height}",
        "font": str(font_path) if font_path else "PIL default",
        "text_style": text_style,
        "text_style_name": TEXT_STYLE_NAMES[text_style],
        "headline": bool(poster_copy.headline),
        "subtitle": bool(poster_copy.subtitle),
        "modules": len(modules),
        "cta": bool(poster_copy.cta),
        **layout_result,
    }


def image_orientation(size: tuple[int, int]) -> str:
    if size[0] > size[1]:
        return "landscape"
    if size[0] < size[1]:
        return "portrait"
    return "square"


def generated_image_audit(path: Path, requested_size: str, width_cm: float, height_cm: float) -> dict[str, Any]:
    with Image.open(path) as image:
        actual_size = image.size
    target_orientation = image_orientation((int(width_cm * 1000), int(height_cm * 1000)))
    actual_orientation = image_orientation(actual_size)
    requested_parts = tuple(int(part) for part in requested_size.split("x"))
    return {
        "requested_px": requested_size,
        "actual_px": f"{actual_size[0]}x{actual_size[1]}",
        "requested_orientation": image_orientation(requested_parts),
        "target_orientation": target_orientation,
        "actual_orientation": actual_orientation,
        "orientation_matches_target": actual_orientation == target_orientation,
        "orientation_matches_request": actual_orientation == image_orientation(requested_parts),
    }


def build_baseline_prompt_context(
    baseline: dict[str, Any],
    user_prompt: str,
    width_cm: float,
    height_cm: float,
    dpi: int,
    image_size: str,
    mode: str,
    poster_copy: PosterCopy,
    text_style: str,
) -> str:
    project = baseline.get("project", {}) if isinstance(baseline, dict) else {}
    consumer = baseline.get("consumer_baseline", {}) if isinstance(baseline, dict) else {}
    visual = baseline.get("visual_guidelines", {}) if isinstance(baseline, dict) else {}
    prompt_policy = baseline.get("prompt_policy", {}) if isinstance(baseline, dict) else {}
    audience = consumer.get("audience", {}) if isinstance(consumer, dict) else {}
    target_orientation = image_orientation((int(width_cm * 1000), int(height_cm * 1000)))
    if target_orientation == "landscape":
        orientation_text = "wide horizontal landscape poster composition"
    elif target_orientation == "portrait":
        orientation_text = "vertical portrait poster composition"
    else:
        orientation_text = "square poster composition"

    if mode == MODE_POSTER:
        task = (
            "Task: Generate one polished background layer for a to-C enrollment poster. "
            "Final Chinese copy will be added by a local typography compositor after image generation."
        )
        user_label = "Visual brief for the background layer:"
        copy_context = [
            "",
            "Local typography compositor will add:",
            f"- Headline present: {'yes' if poster_copy.headline else 'no'}",
            f"- Subtitle present: {'yes' if poster_copy.subtitle else 'no'}",
            f"- Course/module blocks: {len(poster_copy.normalized_modules())}",
            f"- Call-to-action present: {'yes' if poster_copy.cta else 'no'}",
            f"- Local typography style: {TEXT_STYLE_NAMES.get(text_style, text_style)}",
            "Reserve clean, calm areas for these text layers. Do not render the copy inside the image model output.",
        ]
        text_policy = [
            "- Generate background, subject, atmosphere, blank panels, and composition only.",
            "- Leave high-contrast safe areas for title, subtitle, module cards, call-to-action, and optional QR placeholder.",
            "- Do not generate model-made typography, pseudo-text, gibberish, QR codes, phone numbers, prices, logos, watermarks, or signatures.",
        ]
        negative_constraints = [
            "No model-generated readable typography or pseudo-text",
            "No QR code",
            "No logo or real brand mark",
            "No phone number or price",
            "No business meeting or partnership-signing scene",
            "No franchise, revenue, sales, customer acquisition, or school-operation concept",
            "No distorted children, hands, screens, or fake UI text",
            "No watermark or signature",
        ]
    else:
        task = "Task: Generate one polished image-only poster background master for a to-C enrollment poster."
        user_label = "User current request:"
        copy_context = []
        text_policy = [
            "- Generate the background/master artwork only. Leave clean safe areas for headline, course modules, call-to-action, and QR code.",
            "- Do not place final marketing copy, phone numbers, prices, logos, QR codes, watermarks, or signatures in the image.",
            "- If interface panels or module cards appear, use abstract marks and blank glow panels instead of readable text.",
        ]
        negative_constraints = list(prompt_policy.get("negative_constraints", []))

    sections = [
        task,
        f"Project: {project.get('name', 'AI digital creation course')}",
        f"Target audience mode: {baseline.get('target_audience_mode', 'to_c_parent_student')}",
        f"Primary decision maker: {audience.get('primary_decision_maker', 'parents')}",
        f"End user: {audience.get('end_user', 'children and teenagers')}",
        "",
        "Consumer positioning:",
        evidenced_text(consumer.get("positioning")),
        "",
        "Core consumer messages to express visually, without rendering text:",
        *[f"- {item}" for item in text_list(consumer.get("core_messages"))[:5]],
        "",
        "Parent value hints:",
        *[f"- {item}" for item in text_list(consumer.get("parent_value"))[:4]],
        "",
        "Student value hints:",
        *[f"- {item}" for item in text_list(consumer.get("student_value"))[:4]],
        "",
        "Course capabilities to represent as visual elements, icons, screens, scenes, or objects, not readable labels:",
        *[f"- {item}" for item in consumer.get("course_modules", [])],
        "",
        "Recommended visual direction:",
        *[f"- {item}" for item in visual.get("style_keywords", [])],
        *[f"- {item}" for item in visual.get("recommended_subjects", [])],
        *[f"- {item}" for item in visual.get("recommended_scenes", [])],
        "",
        "Composition rules:",
        *[f"- {item}" for item in visual.get("composition_rules", [])],
        "",
        user_label,
        user_prompt,
        *copy_context,
        "",
        "Production constraints:",
        f"- Physical target: {cm_label(width_cm)}cm x {cm_label(height_cm)}cm at {dpi} DPI after print post-processing.",
        f"- Image API master size: {image_size}.",
        f"- Required orientation: {orientation_text}.",
        *text_policy,
        "- The visual must feel suitable for parents and children, not for school operators or business partners.",
        "",
        "Negative constraints:",
        *[f"- {item}" for item in negative_constraints],
    ]
    template = str(prompt_policy.get("positive_prompt_template", "")).strip()
    if template:
        sections.insert(1, f"Baseline positive template: {template}")
    return "\n".join(part for part in sections if part is not None).strip() + "\n"


def build_payload(
    model: str,
    prompt: str,
    image_size: str,
    quality: str,
    output_format: str,
) -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt,
        "size": image_size,
        "quality": quality,
        "output_format": output_format,
    }


def write_run_script(path: Path, request_json_name: str, output_name: str) -> None:
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        : "${{OPENAI_API_KEY:?Set OPENAI_API_KEY before running}}"
        OPENAI_BASE_URL="${{OPENAI_BASE_URL:-https://api.openai.com/v1}}"

        curl -sS "${{OPENAI_BASE_URL%/}}/images/generations" \\
          -H "Authorization: Bearer $OPENAI_API_KEY" \\
          -H "Content-Type: application/json" \\
          -d @{request_json_name} \\
        | python3 -c 'import base64,json,sys,urllib.request; data=json.load(sys.stdin); item=data["data"][0]; payload=base64.b64decode(item["b64_json"]) if item.get("b64_json") else urllib.request.urlopen(item["url"], timeout=900).read(); open("{output_name}","wb").write(payload)'
        """
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _superres_master(
    master_path: Path,
    target_size: "tuple[int, int]",
    realesrgan_binary: "Path | None",
    realesrgan_model_dir: "Path | None",
    realesrgan_model: str,
) -> "tuple[Path, bool]":
    """Return (image_path_for_resize, sr_applied). Runs Real-ESRGAN x4 when
    configured and beneficial (we're upscaling a not-too-large master), else the
    master path unchanged so the caller falls back to plain Lanczos."""
    if not realesrgan_binary or not Path(realesrgan_binary).exists():
        return master_path, False
    if not realesrgan_model_dir or not Path(realesrgan_model_dir).exists():
        return master_path, False
    with Image.open(master_path) as probe:
        mw, mh = probe.size
    # 只在放大且母版不过大时超分
    if mw * mh >= target_size[0] * target_size[1]:
        return master_path, False
    if max(mw, mh) > SR_MAX_INPUT_EDGE or mw * mh > SR_MAX_INPUT_PIXELS:
        return master_path, False
    sr_path = master_path.parent / "_sr" / f"{master_path.stem}_realesrgan_x4.png"
    try:
        run_realesrgan(master_path, sr_path, Path(realesrgan_binary), Path(realesrgan_model_dir), realesrgan_model)
    except Exception as exc:  # noqa: BLE001 - 超分失败退回 PIL，不阻断出图
        print(f"[warn] Real-ESRGAN 超分失败，回退基础缩放：{exc}", file=sys.stderr)
        return master_path, False
    return sr_path, True


def prepare_print_output(
    master_path: Path,
    output_path: Path,
    width_cm: float,
    height_cm: float,
    dpi: int,
    realesrgan_binary: "Path | None" = None,
    realesrgan_model_dir: "Path | None" = None,
    realesrgan_model: str = "realesrgan-x4plus",
) -> dict[str, Any]:
    target_size = target_pixels(width_cm, height_cm, dpi)
    source_path, sr_applied = _superres_master(
        master_path, target_size, realesrgan_binary, realesrgan_model_dir, realesrgan_model
    )
    with Image.open(source_path) as raw:
        icc_profile = raw.info.get("icc_profile")
        image = ImageOps.exif_transpose(raw).convert("RGB")

    delta = aspect_delta_percent(image.size, target_size)
    if abs(delta) <= 1.0:
        prepared = image.resize(target_size, Image.Resampling.LANCZOS)
        layout = "resized"
        content_size = target_size
    else:
        prepared, content_size = fit_with_blurred_background(image, target_size)
        layout = "centered_with_blurred_background"

    prepared = enhance(prepared)
    save_print_image(prepared, output_path, dpi, icc_profile)
    with Image.open(output_path) as saved:
        output_width, output_height = saved.size

    image.close()
    prepared.close()
    if sr_applied and source_path != master_path:
        source_path.unlink(missing_ok=True)
    return {
        "status": "generated",
        "output": str(output_path),
        "backend": "realesrgan_x4plus" if sr_applied else "pil_lanczos",
        "target_cm": f"{cm_label(width_cm)}x{cm_label(height_cm)}",
        "target_dpi": dpi,
        "output_px": f"{output_width}x{output_height}",
        "content_px": f"{content_size[0]}x{content_size[1]}",
        "layout": layout,
        "aspect_delta_percent": round(delta, 2),
    }


def build_package(
    baseline_path: Path,
    output_dir: Path,
    width_cm: float,
    height_cm: float,
    dpi: int,
    user_prompt: str,
    model: str,
    quality: str,
    requested_image_size: str,
    output_format: str,
    execute: bool,
    postprocess_print: bool,
    mode: str,
    text_style: str,
    poster_copy_text: str,
    headline: str | None,
    subtitle: str | None,
    modules: list[str] | None,
    cta: str | None,
    allow_blocked_terms: bool,
    realesrgan_binary: "Path | None" = None,
    realesrgan_model_dir: "Path | None" = None,
    realesrgan_model: str = "realesrgan-x4plus",
) -> Path:
    if mode not in MODE_CHOICES:
        raise ValueError(f"Mode must be one of: {', '.join(MODE_CHOICES)}")
    if text_style not in TEXT_STYLE_CHOICES:
        raise ValueError(f"Text style must be one of: {', '.join(TEXT_STYLE_CHOICES)}")
    if width_cm <= 0 or height_cm <= 0:
        raise ValueError("Width and height must be positive centimeters")
    if dpi <= 0:
        raise ValueError("DPI must be positive")
    if not user_prompt.strip():
        raise ValueError("Prompt must not be empty")

    _stages = ["读取项目基线", "生成提示词", "写请求包"]
    if execute:
        _stages.append("调用图像 API")
        if postprocess_print:
            _stages.append("印刷后处理")
    _stages.append("完成")
    progress.plan(_stages)

    def _advance(label: str) -> None:
        if label in _stages:
            progress.stage(_stages.index(label) + 1)

    _advance("读取项目基线")
    baseline = load_baseline(baseline_path)
    poster_copy = parse_poster_copy(poster_copy_text, headline, subtitle, modules, cta)
    copy_warnings = analyze_poster_copy(poster_copy) if mode == MODE_POSTER else []
    if mode == MODE_POSTER and not poster_copy.has_content():
        raise ValueError("Poster mode requires poster copy. Fill --poster-copy or structured copy fields.")

    blocked_check_text = "\n".join(
        [
            user_prompt,
            poster_copy.raw_text,
            poster_copy.headline,
            poster_copy.subtitle,
            "\n".join(poster_copy.normalized_modules()),
            poster_copy.cta,
        ]
    )
    blocked_terms = blocked_terms_in_prompt(blocked_check_text, baseline)
    if blocked_terms and not allow_blocked_terms:
        terms = "、".join(blocked_terms)
        raise ValueError(f"文生图提示词包含当前 C 端基线禁用词：{terms}")

    _advance("生成提示词")
    image_size = resolve_image_size(width_cm, height_cm, requested_image_size)
    context_source = prompt_context_source(baseline)
    context_hash = profile_hash(context_source)
    prompt = build_baseline_prompt_context(
        baseline,
        user_prompt,
        width_cm,
        height_cm,
        dpi,
        image_size,
        mode,
        poster_copy,
        text_style,
    )
    payload = build_payload(model, prompt, image_size, quality, output_format)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = output_dir / f"{timestamp}_{cm_label(width_cm)}x{cm_label(height_cm)}_{mode}_t2i"
    package_dir.mkdir(parents=True, exist_ok=True)

    master_name = f"master.{output_suffix(output_format)}"
    poster_master_output = package_dir / "poster_master.png"
    print_background_output = package_dir / "print_ready" / (
        f"{cm_label(width_cm)}乘以{cm_label(height_cm)}_文生图背景.jpg"
    )
    poster_print_output = package_dir / "print_ready" / (
        f"{cm_label(width_cm)}乘以{cm_label(height_cm)}_文生图海报.jpg"
    )

    _advance("写请求包")
    (package_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(package_dir / "baseline_context.json", context_source)
    write_json(package_dir / "image_generation_request.json", payload)
    if mode == MODE_POSTER:
        write_json(package_dir / "poster_copy.json", asdict(poster_copy))
        write_json(package_dir / "poster_copy_warnings.json", copy_warnings)
    write_json(
        package_dir / "print_spec.json",
        {
            "width_cm": width_cm,
            "height_cm": height_cm,
            "dpi": dpi,
            "target_px": "%sx%s" % target_pixels(width_cm, height_cm, dpi),
            "image_api_master_size": image_size,
            "output_format": output_format,
            "mode": mode,
            "text_style": text_style if mode == MODE_POSTER else None,
        },
    )
    write_json(
        package_dir / "generation_record.json",
        {
            "baseline_id": baseline.get("baseline_id"),
            "baseline_version": baseline.get("version"),
            "target_audience_mode": baseline.get("target_audience_mode"),
            "profile_hash": context_hash,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "mode": mode,
            "text_style": text_style if mode == MODE_POSTER else None,
            "model": model,
            "size": image_size,
            "quality": quality,
            "execute_requested": execute,
            "postprocess_print_requested": postprocess_print,
            "poster_copy_hash": profile_hash(asdict(poster_copy)) if mode == MODE_POSTER else None,
        },
    )
    write_run_script(package_dir / "run_text_to_image_generation.sh", "image_generation_request.json", master_name)

    status: dict[str, Any] = {
        "mode": mode,
        "image_generation": {"status": "prepared"},
        "print_output": {"status": "not_requested" if not postprocess_print else "pending_image_generation"},
        "poster_master": {"status": "not_requested" if mode == MODE_BACKGROUND else "pending_image_generation"},
        "poster_print_output": {"status": "not_requested" if mode == MODE_BACKGROUND or not postprocess_print else "pending_print_output"},
        "blocked_prompt_terms": blocked_terms,
        "copy_warnings": copy_warnings,
    }
    if execute:
        _advance("调用图像 API")
        status["image_generation"] = execute_image_generation(payload, package_dir / master_name)
        if (
            isinstance(status["image_generation"], dict)
            and status["image_generation"].get("status") == "generated"
        ):
            status["image_generation"].update(
                generated_image_audit(package_dir / master_name, image_size, width_cm, height_cm)
            )
            if mode == MODE_POSTER:
                status["poster_master"] = compose_poster_copy(
                    package_dir / master_name,
                    poster_master_output,
                    poster_copy,
                    dpi,
                    text_style,
                )

            if postprocess_print:
                if not status["image_generation"].get("orientation_matches_target"):
                    status["print_output"] = {
                        "status": "skipped",
                        "reason": "generated master orientation does not match target print orientation",
                        "actual_orientation": status["image_generation"].get("actual_orientation"),
                        "target_orientation": status["image_generation"].get("target_orientation"),
                    }
                    if mode == MODE_POSTER:
                        status["poster_print_output"] = {
                            "status": "skipped",
                            "reason": "print output was skipped because master orientation mismatched target",
                        }
                else:
                    _advance("印刷后处理")
                    status["print_output"] = prepare_print_output(
                        package_dir / master_name,
                        print_background_output,
                        width_cm,
                        height_cm,
                        dpi,
                        realesrgan_binary,
                        realesrgan_model_dir,
                        realesrgan_model,
                    )
                    if mode == MODE_POSTER:
                        status["poster_print_output"] = compose_poster_copy(
                            print_background_output,
                            poster_print_output,
                            poster_copy,
                            dpi,
                            text_style,
                        )
        elif postprocess_print:
            status["print_output"] = {
                "status": "skipped",
                "reason": "master image was not generated",
            }
            if mode == MODE_POSTER:
                status["poster_print_output"] = {
                    "status": "skipped",
                    "reason": "master image was not generated",
                }
    elif not execute:
        status["image_generation"] = {
            "status": "prepared_not_executed",
            "reason": "Run with --execute or use run_text_to_image_generation.sh after configuring OPENAI_API_KEY.",
        }
        if postprocess_print:
            status["print_output"] = {
                "status": "skipped",
                "reason": "master image was not generated",
            }
        if mode == MODE_POSTER:
            status["poster_master"] = {
                "status": "skipped",
                "reason": "master image was not generated",
            }
            if postprocess_print:
                status["poster_print_output"] = {
                    "status": "skipped",
                    "reason": "master image was not generated",
                }

    _advance("完成")
    write_json(package_dir / "status.json", status)
    readme = textwrap.dedent(
        f"""
        # Baseline Text-to-Image Package

        This package generates a to-C poster asset from the current project
        baseline and a user prompt.

        Files:
        - `prompt.md`: final baseline-aware image prompt.
        - `baseline_context.json`: exact baseline fields injected into the prompt.
        - `image_generation_request.json`: Image API request payload.
        - `generation_record.json`: baseline/model/profile metadata for traceability.
        - `status.json`: execution result.

        Mode: `{mode}`.

        In `background` mode, the prompt asks the image model to create a clean
        no-text background. In `poster` mode, the image model still creates the
        background layer only, and DashDesign composes exact Chinese copy locally
        into `poster_master.png` and/or the print-ready poster output.
        """
    ).strip()
    (package_dir / "README.md").write_text(readme + "\n", encoding="utf-8")
    progress.done(str(package_dir))
    return package_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--width-cm", type=float, required=True)
    parser.add_argument("--height-cm", type=float, required=True)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--prompt", required=True, help="Current text-to-image request.")
    parser.add_argument(
        "--mode",
        choices=MODE_CHOICES,
        default=MODE_BACKGROUND,
        help="background outputs a no-text background; poster composites local Chinese copy after generation.",
    )
    parser.add_argument(
        "--text-style",
        choices=TEXT_STYLE_CHOICES,
        default=TEXT_STYLE_CLEAN_EDU,
        help="Local typography template used in poster mode.",
    )
    parser.add_argument("--poster-copy", default="", help="Raw poster copy for poster mode.")
    parser.add_argument("--headline", help="Poster headline for poster mode.")
    parser.add_argument("--subtitle", help="Poster subtitle for poster mode.")
    parser.add_argument(
        "--module",
        action="append",
        dest="modules",
        help="Poster module/copy block. Can be passed multiple times.",
    )
    parser.add_argument("--cta", help="Poster call-to-action for poster mode.")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--quality", default="high", choices=("low", "medium", "high", "auto"))
    parser.add_argument("--image-size", default="auto", help="auto or WIDTHxHEIGHT.")
    parser.add_argument("--output-format", default="png", choices=("png", "jpeg", "webp"))
    parser.add_argument("--execute", action="store_true", help="Call the Image API now.")
    parser.add_argument(
        "--postprocess-print",
        action="store_true",
        help="After API generation, resize the master to the requested print pixels.",
    )
    parser.add_argument(
        "--realesrgan-binary",
        type=Path,
        default=Path("tools/realesrgan-ncnn-vulkan"),
        help="Real-ESRGAN 二进制；印刷后处理时用它做超分（存在则启用，否则回退 PIL）。",
    )
    parser.add_argument("--realesrgan-model-dir", type=Path, default=Path("tools/models"))
    parser.add_argument("--realesrgan-model", default="realesrgan-x4plus")
    parser.add_argument(
        "--allow-blocked-terms",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        package_dir = build_package(
            args.baseline.resolve(),
            args.output_dir.resolve(),
            args.width_cm,
            args.height_cm,
            args.dpi,
            args.prompt.strip(),
            args.model,
            args.quality,
            args.image_size,
            args.output_format,
            args.execute,
            args.postprocess_print,
            args.mode,
            args.text_style,
            args.poster_copy,
            args.headline,
            args.subtitle,
            args.modules,
            args.cta,
            args.allow_blocked_terms,
            realesrgan_binary=args.realesrgan_binary,
            realesrgan_model_dir=args.realesrgan_model_dir,
            realesrgan_model=args.realesrgan_model,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Package written to {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
