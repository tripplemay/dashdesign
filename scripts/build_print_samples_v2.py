#!/usr/bin/env python3
"""Build region-aware 200dpi print samples.

This is a prototype for the v2 production workflow:

1. Enhance the approved source artwork with Real-ESRGAN.
2. Normalize it to the physical print size parsed from the filename.
3. Rebuild selected text and QR regions as deterministic print layers.

The text layer configs below are intentionally explicit. They are sample-layer
specs for the three agreed proof images, not a general OCR replacement yet.
"""

from __future__ import annotations

import argparse
import csv
import gc
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import qrcode
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from prepare_print_assets import (
    aspect_delta_percent,
    parse_size_from_name,
    target_pixels,
)


Image.MAX_IMAGE_PIXELS = None

WECHAT_QR_PAYLOAD = "https://u.wechat.com/EHpndEVfc8fKpkh-fX_eOfQ?s=2"
DEFAULT_FONT = Path("/System/Library/Fonts/STHeiti Medium.ttc")
ALT_FONT = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")


@dataclass(frozen=True)
class TextLayer:
    box: tuple[int, int, int, int]
    text: str
    font_size: int
    fill: tuple[int, int, int]
    stroke_fill: tuple[int, int, int] = (14, 23, 55)
    stroke_width: float = 1.0
    shadow_fill: tuple[int, int, int] | None = (0, 0, 0)
    shadow_offset: tuple[float, float] = (1.5, 2.0)
    align: str = "center"
    valign: str = "center"
    spacing: float = 0.18
    fit: bool = True
    erase: bool = True
    erase_expand: float = 4.0
    erase_blur: float = 9.0
    erase_box: tuple[int, int, int, int] | None = None
    erase_mode: str = "panel"
    panel_fill: tuple[int, int, int, int] = (7, 13, 36, 220)


@dataclass(frozen=True)
class QRLayer:
    box: tuple[int, int, int, int]
    payload: str
    fill: tuple[int, int, int] = (255, 255, 255)
    outline: tuple[int, int, int] = (255, 255, 255)
    padding: float = 0.08
    erase_expand: float = 3.0


@dataclass(frozen=True)
class SampleConfig:
    source: str
    output_name: str
    notes: str
    text_layers: list[TextLayer] = field(default_factory=list)
    qr_layers: list[QRLayer] = field(default_factory=list)


SAMPLES: list[SampleConfig] = [
    SampleConfig(
        source="160乘以160.jpg",
        output_name="160乘以160_v2_rebuild.jpg",
        notes="Square poster: rebuilds title, feature copy, bottom offer, and QR.",
        text_layers=[
            TextLayer(
                box=(120, 42, 1160, 260),
                text="AI浪潮已来，\n孩子的学习怎能落后?",
                font_size=88,
                fill=(255, 245, 185),
                stroke_fill=(154, 42, 43),
                stroke_width=2.4,
                shadow_fill=(82, 22, 28),
                shadow_offset=(2.0, 2.8),
                spacing=0.03,
            ),
            TextLayer(
                box=(155, 445, 455, 560),
                text="AI绘图:输入文字,\n一键生成精美画作,\n让孩子成为创意画师!",
                font_size=31,
                fill=(255, 255, 255),
                stroke_fill=(25, 33, 85),
                stroke_width=1.3,
                align="left",
                spacing=0.14,
            ),
            TextLayer(
                box=(855, 445, 1210, 565),
                text="AI视频:轻松创作专属动画\n与小视频，记录成长,\n秀出才华!",
                font_size=29,
                fill=(255, 255, 255),
                stroke_fill=(25, 33, 85),
                stroke_width=1.2,
                align="center",
                spacing=0.14,
            ),
            TextLayer(
                box=(70, 720, 405, 845),
                text="AI漫剧：从剧本到分镜,\nAI助你成为动漫大师,\n让故事活起来!",
                font_size=30,
                fill=(255, 255, 255),
                stroke_fill=(26, 33, 84),
                stroke_width=1.2,
                align="center",
                spacing=0.14,
            ),
            TextLayer(
                box=(862, 724, 1220, 850),
                text="AI编程:零基础入门,\n掌握Python与网页开发,\n打造未来核心竞争力!",
                font_size=28,
                fill=(255, 255, 255),
                stroke_fill=(26, 33, 84),
                stroke_width=1.2,
                align="center",
                spacing=0.14,
            ),
            TextLayer(
                box=(170, 875, 1068, 1072),
                text="AI是未来的核心语言!\n现在不学，孩子未来就会像“文盲”一样!\n别让孩子输在AI时代的起跑。",
                font_size=47,
                fill=(255, 250, 204),
                stroke_fill=(181, 31, 28),
                stroke_width=2.0,
                shadow_fill=(85, 11, 24),
                shadow_offset=(1.8, 2.4),
                spacing=0.08,
            ),
            TextLayer(
                box=(250, 1125, 940, 1232),
                text="限时福利  前50名扫码预约，即可获得\n【免费AI体验课】名额有限，名额有限!",
                font_size=36,
                fill=(255, 255, 255),
                stroke_fill=(38, 28, 70),
                stroke_width=1.4,
                spacing=0.12,
            ),
        ],
        qr_layers=[
            QRLayer(box=(990, 990, 1248, 1248), payload=WECHAT_QR_PAYLOAD),
        ],
    ),
    SampleConfig(
        source="200乘以80.jpg",
        output_name="200乘以80_v2_rebuild.jpg",
        notes="Wide banner: rebuilds dense curriculum copy over Real-ESRGAN artwork.",
        text_layers=[
            TextLayer(
                box=(730, 42, 2508, 205),
                text="传统编程=AI科创课程",
                font_size=92,
                fill=(255, 255, 245),
                stroke_fill=(29, 82, 114),
                stroke_width=1.8,
                shadow_fill=(7, 19, 42),
                shadow_offset=(2.0, 2.5),
            ),
            TextLayer(
                box=(215, 155, 780, 248),
                text="传统编程",
                font_size=65,
                fill=(255, 255, 255),
                stroke_fill=(20, 58, 85),
                stroke_width=1.4,
            ),
            TextLayer(
                box=(180, 278, 760, 388),
                text="【核心定位】\n夯实计算机底层逻辑基础\n扎根代码原生原理，搭建完整的程序技术底层功底",
                font_size=34,
                fill=(255, 255, 255),
                stroke_fill=(14, 41, 70),
                stroke_width=1.1,
                align="left",
                spacing=0.16,
            ),
            TextLayer(
                box=(170, 455, 950, 545),
                text="【学习核心内容】\nScratch图形化编程、Python、C语言\n代码语法、数据算法、程序架构设计",
                font_size=32,
                fill=(255, 255, 255),
                stroke_fill=(14, 41, 70),
                stroke_width=1.1,
                align="left",
                spacing=0.16,
            ),
            TextLayer(
                box=(260, 630, 900, 735),
                text="【能力培养核心】\n严谨的逻辑思维、专业扎实的手写编程\n能力、条理缜密的问题拆解与解决能力",
                font_size=31,
                fill=(255, 255, 255),
                stroke_fill=(14, 41, 70),
                stroke_width=1.1,
                align="left",
                spacing=0.16,
            ),
            TextLayer(
                box=(650, 280, 1045, 480),
                text="【价值优势】\n夯实基本功，适配蓝桥杯\n等专业科创比赛\n适合:零基础入门想要冲刺\n竞赛的孩子",
                font_size=32,
                fill=(255, 255, 255),
                stroke_fill=(14, 41, 70),
                stroke_width=1.1,
                align="left",
                spacing=0.14,
            ),
            TextLayer(
                box=(2450, 145, 2980, 245),
                text="AI课程",
                font_size=66,
                fill=(255, 255, 255),
                stroke_fill=(120, 45, 20),
                stroke_width=1.4,
            ),
            TextLayer(
                box=(1485, 300, 2145, 480),
                text="【价值优势】\n拥抱人工智能时代创新应用图\n像识别、创意项目实战\n适合:适应AI新时代孩子",
                font_size=32,
                fill=(255, 255, 255),
                stroke_fill=(106, 39, 18),
                stroke_width=1.1,
                align="left",
                spacing=0.15,
            ),
            TextLayer(
                box=(2300, 278, 3135, 415),
                text="【核心定位】\n立足人工智能时代应用落地\n依托AI工具实现创意数字化创作，聚焦实战化成品\n产出，适配当下科技时代的创作型编程需求",
                font_size=30,
                fill=(255, 255, 255),
                stroke_fill=(106, 39, 18),
                stroke_width=1.05,
                align="left",
                spacing=0.14,
            ),
            TextLayer(
                box=(2290, 498, 3150, 610),
                text="【学习核心内容】\nAI智能绘图、AI短视频制作、AI漫剧动画创作,\nAI辅助网页开发、AI小程序开发、智能工具协同\n编程、提示词工程、AI生成逻辑调控",
                font_size=28,
                fill=(255, 255, 255),
                stroke_fill=(106, 39, 18),
                stroke_width=1.0,
                align="left",
                spacing=0.12,
            ),
            TextLayer(
                box=(2265, 665, 3135, 780),
                text="【能力培养核心】\n创意发散与艺术设计思维、AI工具协同的高效落地\n创作能力、结合科技与创意的综合项目策划与成品\n实现能力",
                font_size=28,
                fill=(255, 255, 255),
                stroke_fill=(106, 39, 18),
                stroke_width=1.0,
                align="left",
                spacing=0.12,
            ),
            TextLayer(
                box=(720, 1075, 2500, 1262),
                text="只学传统编程，缺乏时代创新，想要提升综合创新能力的孩子\n单学AI工具，没有底层根基。",
                font_size=48,
                fill=(255, 255, 250),
                stroke_fill=(109, 45, 19),
                stroke_width=1.3,
                spacing=0.12,
            ),
        ],
    ),
    SampleConfig(
        source="80乘以180 （打孔）.jpg",
        output_name="80乘以180_打孔_v2_rebuild.jpg",
        notes="Very low-resolution vertical poster: rebuilds title, main copy, offer copy, and QR.",
        text_layers=[
            TextLayer(
                box=(20, 55, 418, 190),
                text="AI浪潮已来,\n孩子的学习怎能落后?",
                font_size=42,
                fill=(255, 243, 188),
                stroke_fill=(148, 45, 39),
                stroke_width=1.8,
                shadow_fill=(70, 21, 32),
                shadow_offset=(1.5, 2.0),
                spacing=0.04,
                erase_box=(12, 45, 424, 235),
                erase_expand=6.0,
            ),
            TextLayer(
                box=(8, 500, 225, 600),
                text="AI绘图:输入文字，一键\n生成精美画作，让孩子\n成为创意画师!",
                font_size=18,
                fill=(255, 255, 255),
                stroke_fill=(24, 42, 89),
                stroke_width=0.9,
                align="center",
                spacing=0.12,
            ),
            TextLayer(
                box=(268, 430, 420, 548),
                text="AI视频:轻松创作专属动画\n画与小视频，记录成长,\n秀出才华!",
                font_size=15,
                fill=(255, 255, 255),
                stroke_fill=(24, 42, 89),
                stroke_width=0.8,
                align="center",
                spacing=0.1,
            ),
            TextLayer(
                box=(235, 710, 420, 800),
                text="AI漫剧：从剧本到分镜,\nAI助你成为动漫大师，让\n故事活起来!",
                font_size=16,
                fill=(255, 255, 255),
                stroke_fill=(24, 42, 89),
                stroke_width=0.8,
                align="center",
                spacing=0.1,
            ),
            TextLayer(
                box=(25, 978, 405, 1090),
                text="AI是未来的核心语言!\n现在不学，孩子未来就会像“文盲”\n一样! 别让孩子输在AI时代的起跑!",
                font_size=24,
                fill=(255, 250, 210),
                stroke_fill=(168, 35, 31),
                stroke_width=1.4,
                shadow_fill=(75, 18, 26),
                shadow_offset=(1.2, 1.8),
                spacing=0.08,
                erase_box=(10, 955, 418, 1110),
                erase_expand=5.0,
            ),
            TextLayer(
                box=(80, 1132, 350, 1190),
                text="限时福利",
                font_size=30,
                fill=(255, 235, 120),
                stroke_fill=(97, 43, 24),
                stroke_width=1.3,
                shadow_offset=(1.0, 1.4),
                erase_box=(58, 1115, 366, 1198),
                erase_expand=5.0,
            ),
            TextLayer(
                box=(22, 1200, 405, 1262),
                text="前50名扫码预约，即可获得【免费AI体验课】\n名额有限，先到先得!",
                font_size=15,
                fill=(255, 255, 255),
                stroke_fill=(36, 28, 65),
                stroke_width=0.8,
                spacing=0.08,
                erase_box=(8, 1195, 418, 1275),
                erase_expand=5.0,
            ),
        ],
        qr_layers=[
            QRLayer(box=(128, 820, 305, 1002), payload=WECHAT_QR_PAYLOAD),
        ],
    ),
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    font_path = DEFAULT_FONT if DEFAULT_FONT.exists() else ALT_FONT
    return ImageFont.truetype(str(font_path), size=size, index=0)


def run_realesrgan(source: Path, destination: Path, args: argparse.Namespace) -> None:
    if destination.exists() and not args.force:
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(args.realesrgan_binary),
        "-i",
        str(source),
        "-o",
        str(destination),
        "-m",
        str(args.realesrgan_model_dir),
        "-s",
        "4",
        "-n",
        args.realesrgan_model,
        "-t",
        str(args.tile_size),
        "-j",
        "1:1:1",
        "-f",
        "png",
    ]
    subprocess.run(
        command,
        check=True,
        timeout=args.timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def place_enhanced_background(
    source_path: Path,
    enhanced_path: Path,
    dpi: int,
    aspect_tolerance: float,
) -> tuple[Image.Image, Callable[[tuple[int, int, int, int]], tuple[int, int, int, int]], dict[str, str | int | float]]:
    physical_size = parse_size_from_name(source_path)
    if not physical_size:
        raise ValueError(f"Could not parse physical size from filename: {source_path.name}")

    with Image.open(source_path) as raw:
        source_size = ImageOps.exif_transpose(raw).size
    with Image.open(enhanced_path) as raw:
        enhanced = ImageOps.exif_transpose(raw).convert("RGB")

    target_size = target_pixels(physical_size[0], physical_size[1], dpi)
    delta = aspect_delta_percent(source_size, target_size)

    if abs(delta) <= aspect_tolerance:
        canvas = enhanced.resize(target_size, Image.Resampling.LANCZOS)
        scale_x = target_size[0] / source_size[0]
        scale_y = target_size[1] / source_size[1]
        offset_x = 0
        offset_y = 0
        content_size = target_size
        layout = "resized"
    else:
        background = ImageOps.fit(
            enhanced,
            target_size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        blur_radius = max(18, int(max(target_size) * 0.018))
        background = background.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        background = ImageEnhance.Contrast(background).enhance(0.82)
        background = ImageEnhance.Brightness(background).enhance(0.86)

        scale = min(target_size[0] / source_size[0], target_size[1] / source_size[1])
        content_size = (
            max(1, round(source_size[0] * scale)),
            max(1, round(source_size[1] * scale)),
        )
        content = enhanced.resize(content_size, Image.Resampling.LANCZOS)
        offset_x = (target_size[0] - content_size[0]) // 2
        offset_y = (target_size[1] - content_size[1]) // 2
        background.paste(content, (offset_x, offset_y))
        canvas = background
        scale_x = scale
        scale_y = scale
        layout = "centered_with_blurred_background"
        content.close()

    def map_box(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        left, top, right, bottom = box
        return (
            round(offset_x + left * scale_x),
            round(offset_y + top * scale_y),
            round(offset_x + right * scale_x),
            round(offset_y + bottom * scale_y),
        )

    metadata: dict[str, str | int | float] = {
        "target_cm": f"{physical_size[0]}x{physical_size[1]}",
        "source_px": f"{source_size[0]}x{source_size[1]}",
        "output_px": f"{target_size[0]}x{target_size[1]}",
        "content_px": f"{content_size[0]}x{content_size[1]}",
        "scale_x": round(scale_x, 4),
        "scale_y": round(scale_y, 4),
        "aspect_delta_percent": round(delta, 2),
        "layout": layout,
    }
    enhanced.close()
    return canvas, map_box, metadata


def fit_font_to_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    base_size: int,
    box_size: tuple[int, int],
    stroke_width: int,
    spacing_ratio: float,
) -> tuple[ImageFont.FreeTypeFont, int]:
    font_size = max(8, base_size)
    while font_size > 8:
        font = load_font(font_size)
        spacing = max(0, round(font_size * spacing_ratio))
        bbox = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke_width, spacing=spacing)
        if bbox[2] - bbox[0] <= box_size[0] and bbox[3] - bbox[1] <= box_size[1]:
            return font, spacing
        font_size = int(font_size * 0.94)
    return load_font(font_size), max(0, round(font_size * spacing_ratio))


def draw_text_layer(
    image: Image.Image,
    layer: TextLayer,
    map_box: Callable[[tuple[int, int, int, int]], tuple[int, int, int, int]],
    scale_hint: float,
) -> None:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = map_box(layer.box)
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)
    font_size = max(8, round(layer.font_size * scale_hint))
    stroke_width = max(1, round(layer.stroke_width * scale_hint))

    if layer.fit:
        font, spacing = fit_font_to_box(draw, layer.text, font_size, (box_w, box_h), stroke_width, layer.spacing)
    else:
        font = load_font(font_size)
        spacing = max(0, round(font_size * layer.spacing))

    bbox = draw.multiline_textbbox((0, 0), layer.text, font=font, stroke_width=stroke_width, spacing=spacing)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    if layer.align == "left":
        x = left - bbox[0]
    elif layer.align == "right":
        x = right - text_w - bbox[0]
    else:
        x = left + (box_w - text_w) / 2 - bbox[0]

    if layer.valign == "top":
        y = top - bbox[1]
    elif layer.valign == "bottom":
        y = bottom - text_h - bbox[1]
    else:
        y = top + (box_h - text_h) / 2 - bbox[1]

    if layer.shadow_fill is not None:
        shadow_x = x + layer.shadow_offset[0] * scale_hint
        shadow_y = y + layer.shadow_offset[1] * scale_hint
        draw.multiline_text(
            (shadow_x, shadow_y),
            layer.text,
            font=font,
            fill=layer.shadow_fill,
            spacing=spacing,
            align=layer.align,
            stroke_width=stroke_width,
            stroke_fill=layer.shadow_fill,
        )

    draw.multiline_text(
        (x, y),
        layer.text,
        font=font,
        fill=layer.fill,
        spacing=spacing,
        align=layer.align,
        stroke_width=stroke_width,
        stroke_fill=layer.stroke_fill,
    )


def soft_erase_box(
    image: Image.Image,
    box: tuple[int, int, int, int],
    expand: int,
    blur_radius: int,
) -> None:
    left, top, right, bottom = box
    left = max(0, left - expand)
    top = max(0, top - expand)
    right = min(image.width, right + expand)
    bottom = min(image.height, bottom + expand)
    if right <= left or bottom <= top:
        return

    region = image.crop((left, top, right, bottom))
    blur_radius = max(2, blur_radius)
    softened = region.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    softened = ImageEnhance.Contrast(softened).enhance(0.86)
    softened = ImageEnhance.Brightness(softened).enhance(0.92)
    image.paste(softened, (left, top))
    region.close()
    softened.close()


def panel_erase_box(
    image: Image.Image,
    box: tuple[int, int, int, int],
    expand: int,
    scale_hint: float,
    fill: tuple[int, int, int, int],
) -> None:
    left, top, right, bottom = box
    left = max(0, left - expand)
    top = max(0, top - expand)
    right = min(image.width, right + expand)
    bottom = min(image.height, bottom + expand)
    if right <= left or bottom <= top:
        return

    region = image.crop((left, top, right, bottom)).convert("RGBA")
    region = region.filter(ImageFilter.GaussianBlur(radius=max(1, round(0.7 * scale_hint))))
    overlay = Image.new("RGBA", region.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    radius = max(8, round(4.0 * scale_hint))
    draw.rounded_rectangle((0, 0, region.width, region.height), radius=radius, fill=fill)
    merged = Image.alpha_composite(region, overlay).convert("RGB")
    image.paste(merged, (left, top))
    region.close()
    overlay.close()
    merged.close()


def inpaint_text_box(
    image: Image.Image,
    box: tuple[int, int, int, int],
    expand: int,
    scale_hint: float,
) -> bool:
    try:
        import cv2  # type: ignore
    except Exception:
        return False

    left, top, right, bottom = box
    left = max(0, left - expand)
    top = max(0, top - expand)
    right = min(image.width, right + expand)
    bottom = min(image.height, bottom + expand)
    if right <= left or bottom <= top:
        return False

    region = image.crop((left, top, right, bottom)).convert("RGB")
    rgb = np.asarray(region)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    threshold = max(135, int(np.percentile(gray, 68)))
    bright_mask = gray >= threshold
    saturated_text_mask = (saturation >= 62) & (value >= 105)
    mask = (bright_mask | saturated_text_mask).astype("uint8") * 255

    kernel_size = max(3, round(2.2 * scale_hint))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=2)
    mask = cv2.medianBlur(mask, 5)

    masked_ratio = float(np.mean(mask > 0))
    if masked_ratio < 0.015 or masked_ratio > 0.78:
        region.close()
        return False

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    restored = cv2.inpaint(bgr, mask, inpaintRadius=max(3, round(1.6 * scale_hint)), flags=cv2.INPAINT_TELEA)
    restored = cv2.cvtColor(restored, cv2.COLOR_BGR2RGB)
    image.paste(Image.fromarray(restored), (left, top))
    region.close()
    return True


def erase_text_layer(
    image: Image.Image,
    layer: TextLayer,
    map_box: Callable[[tuple[int, int, int, int]], tuple[int, int, int, int]],
    scale_hint: float,
) -> None:
    if not layer.erase:
        return
    final_box = map_box(layer.erase_box or layer.box)
    expand = max(2, round(layer.erase_expand * scale_hint))
    if layer.erase_mode == "panel":
        panel_erase_box(image, final_box, expand=expand, scale_hint=scale_hint, fill=layer.panel_fill)
        return
    if layer.erase_mode == "none":
        return
    if inpaint_text_box(image, final_box, expand, scale_hint):
        return
    soft_erase_box(image, final_box, expand=expand, blur_radius=max(3, round(layer.erase_blur * scale_hint)))


def make_qr(payload: str, size: int) -> Image.Image:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=20,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return image.resize((size, size), Image.Resampling.NEAREST)


def draw_qr_layer(
    image: Image.Image,
    layer: QRLayer,
    map_box: Callable[[tuple[int, int, int, int]], tuple[int, int, int, int]],
) -> None:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = map_box(layer.box)
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)
    pad = max(0, round(min(box_w, box_h) * layer.padding))
    draw.rectangle((left, top, right, bottom), fill=layer.fill, outline=layer.outline, width=max(1, pad // 5))
    qr_size = max(1, min(box_w, box_h) - pad * 2)
    qr = make_qr(layer.payload, qr_size)
    image.paste(qr, (left + (box_w - qr_size) // 2, top + (box_h - qr_size) // 2))
    qr.close()


def polish_preserved_text_layer(
    image: Image.Image,
    layer: TextLayer,
    map_box: Callable[[tuple[int, int, int, int]], tuple[int, int, int, int]],
    scale_hint: float,
) -> None:
    box = map_box(layer.erase_box or layer.box)
    expand = max(2, round(layer.erase_expand * scale_hint))
    left, top, right, bottom = box
    left = max(0, left - expand)
    top = max(0, top - expand)
    right = min(image.width, right + expand)
    bottom = min(image.height, bottom + expand)
    if right <= left or bottom <= top:
        return

    region = image.crop((left, top, right, bottom)).convert("RGB")
    sharpened = ImageEnhance.Contrast(region).enhance(1.035)
    sharpened = sharpened.filter(ImageFilter.UnsharpMask(radius=0.55, percent=95, threshold=2))

    arr = np.asarray(region, dtype=np.uint8)
    max_channel = arr.max(axis=2).astype(np.int16)
    min_channel = arr.min(axis=2).astype(np.int16)
    saturation = max_channel - min_channel
    gray = (
        0.299 * arr[:, :, 0]
        + 0.587 * arr[:, :, 1]
        + 0.114 * arr[:, :, 2]
    )
    threshold = max(118, int(np.percentile(gray, 64)))
    mask_array = ((gray >= threshold) | ((saturation >= 46) & (gray >= 72))).astype(np.uint8) * 255
    mask = Image.fromarray(mask_array, mode="L")
    filter_size = max(3, round(1.3 * scale_hint))
    if filter_size % 2 == 0:
        filter_size += 1
    mask = mask.filter(ImageFilter.MaxFilter(filter_size)).filter(ImageFilter.GaussianBlur(radius=max(1, round(0.35 * scale_hint))))

    polished = Image.composite(sharpened, region, mask)
    image.paste(polished, (left, top))
    region.close()
    sharpened.close()
    mask.close()
    polished.close()


def erase_qr_layer(
    image: Image.Image,
    layer: QRLayer,
    map_box: Callable[[tuple[int, int, int, int]], tuple[int, int, int, int]],
    scale_hint: float,
) -> None:
    final_box = map_box(layer.box)
    soft_erase_box(
        image,
        final_box,
        expand=max(2, round(layer.erase_expand * scale_hint)),
        blur_radius=max(3, round(10 * scale_hint)),
    )


def save_jpeg(image: Image.Image, destination: Path, dpi: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(
        destination,
        quality=95,
        subsampling=0,
        progressive=True,
        optimize=True,
        dpi=(dpi, dpi),
    )


def save_review(image: Image.Image, destination: Path, max_edge: int, dpi: int) -> None:
    review = image.copy()
    review.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    save_jpeg(review, destination, dpi)
    review.close()


def build_contact_sheet(review_paths: list[Path], destination: Path) -> None:
    tiles: list[Image.Image] = []
    for path in review_paths:
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
        tile_w = 520
        tile_h = 440
        label_h = 46
        image.thumbnail((tile_w, tile_h - label_h), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (tile_w, tile_h), "white")
        tile.paste(image, ((tile_w - image.width) // 2, 0))
        draw = ImageDraw.Draw(tile)
        draw.text((10, tile_h - label_h + 8), path.name, fill=(0, 0, 0))
        tiles.append(tile)

    if not tiles:
        return
    sheet = Image.new("RGB", (tiles[0].width * len(tiles), tiles[0].height), "white")
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (index * tile.width, 0))
        tile.close()
    save_jpeg(sheet, destination, 96)
    sheet.close()


def sample_output_name(config: SampleConfig, args: argparse.Namespace) -> str:
    if args.text_mode == "preserve":
        return config.output_name.replace("_v2_rebuild", "_v3_style_preserved")
    return config.output_name


def process_sample(config: SampleConfig, args: argparse.Namespace) -> dict[str, str | int | float]:
    source_path = args.input_dir / config.source
    scratch_path = args.output_dir / "_masters" / f"{source_path.stem}_realesrgan_x4.png"
    run_realesrgan(source_path, scratch_path, args)

    image, map_box, metadata = place_enhanced_background(
        source_path,
        scratch_path,
        args.dpi,
        args.aspect_tolerance,
    )
    scale_hint = float(metadata["scale_x"])

    if args.text_mode == "rebuild":
        for layer in config.text_layers:
            erase_text_layer(image, layer, map_box, scale_hint)

        for layer in config.qr_layers:
            erase_qr_layer(image, layer, map_box, scale_hint)

        for layer in config.text_layers:
            draw_text_layer(image, layer, map_box, scale_hint)
    elif args.text_mode == "preserve":
        for layer in config.text_layers:
            polish_preserved_text_layer(image, layer, map_box, scale_hint)

        for layer in config.qr_layers:
            erase_qr_layer(image, layer, map_box, scale_hint)
    else:
        raise ValueError(f"Unsupported text mode: {args.text_mode}")

    for layer in config.qr_layers:
        draw_qr_layer(image, layer, map_box)

    output_name = sample_output_name(config, args)
    output_path = args.output_dir / output_name
    review_path = args.output_dir / "review" / output_name
    save_jpeg(image, output_path, args.dpi)
    save_review(image, review_path, args.review_max_edge, args.dpi)

    row: dict[str, str | int | float] = {
        "source": config.source,
        "output": str(output_path),
        "review": str(review_path),
        "text_layers": len(config.text_layers),
        "qr_layers": len(config.qr_layers),
        "text_mode": args.text_mode,
        "notes": config.notes,
    }
    row.update(metadata)

    image.close()
    gc.collect()
    return row


def write_report(output_dir: Path, rows: list[dict[str, str | int | float]], dpi: int, text_mode: str) -> None:
    csv_path = output_dir / "sample_audit.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        "# Print Sample Notes",
        "",
        f"Generated sample count: {len(rows)}",
        f"Target DPI: {dpi}",
        f"Text mode: `{text_mode}`",
        "",
        "Workflow:",
        "",
        "1. Real-ESRGAN `x4plus` enhances the source artwork layer.",
        "2. The enhanced image is fitted to the print pixel size parsed from the filename.",
        "3. Depending on text mode, selected text is either rebuilt or style-preserved and locally polished.",
        "4. QR regions are rebuilt as deterministic high-resolution layers.",
        "",
        "Important limitations:",
        "",
        "- This is a proof workflow with manually defined text regions for three images.",
        "- `rebuild` mode renders crisp text but does not preserve the original typography/effects unless exact font and style templates are supplied.",
        "- `preserve` mode keeps the original typography/effects, but cannot fully recover unreadable source text.",
        "- The earlier rebuild proof used semi-transparent text plates to suppress old low-resolution text; the production version should replace that with cleaner background reconstruction or editable source layers.",
        "- For production, OCR output still needs manual copy verification before batch rendering.",
        "- QR codes are regenerated from the decoded payload where available.",
        "",
        "Outputs:",
        "",
        "- Full 200dpi print samples: files in this directory.",
        "- Review previews: `review/*.jpg`.",
        "- Contact sheet: `review/contact_sheet.jpg`.",
        "- Audit table: `sample_audit.csv`.",
        "",
    ]
    output_dir.joinpath("README.md").write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("print_samples_v2_200dpi"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--aspect-tolerance", type=float, default=0.5)
    parser.add_argument("--review-max-edge", type=int, default=2200)
    parser.add_argument("--realesrgan-binary", type=Path, default=Path("tools/realesrgan-ncnn-vulkan"))
    parser.add_argument("--realesrgan-model-dir", type=Path, default=Path("tools/models"))
    parser.add_argument("--realesrgan-model", default="realesrgan-x4plus")
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--clean-masters", action="store_true")
    parser.add_argument(
        "--text-mode",
        choices=["rebuild", "preserve"],
        default="rebuild",
        help="rebuild text with deterministic font layers, or preserve original typography and locally polish it.",
    )
    parser.add_argument(
        "--only",
        action="append",
        help="Process only matching source filename. Can be repeated.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    selected = [
        sample
        for sample in SAMPLES
        if not args.only or sample.source in args.only or sample.output_name in args.only
    ]
    if not selected:
        raise SystemExit("No matching samples selected")

    rows = [process_sample(sample, args) for sample in selected]
    write_report(args.output_dir, rows, args.dpi, args.text_mode)

    review_paths = [Path(row["review"]) for row in rows]
    build_contact_sheet(review_paths, args.output_dir / "review" / "contact_sheet.jpg")

    if args.clean_masters:
        shutil.rmtree(args.output_dir / "_masters", ignore_errors=True)


if __name__ == "__main__":
    main()
