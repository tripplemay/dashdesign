#!/usr/bin/env python3
"""Build a GPT Image rebuild package for one print asset.

This prepares the second workflow direction: use an original low-resolution
poster as a reference, generate a clean high-quality image-model master, then
feed that master back into the print-prep pipeline.

The script can create offline request packages without an API key. If
``--execute`` is passed and ``OPENAI_API_KEY`` is set, it calls the Image API
directly with ``requests`` and saves the generated master.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

import progress

from image_api_client import execute_image_request
from prepare_print_assets import parse_size_from_name, target_pixels


MAX_GPT_IMAGE_PIXELS = 8_294_400
MAX_GPT_IMAGE_EDGE = 3840
MIN_GPT_IMAGE_PIXELS = 655_360


@dataclass(frozen=True)
class ImageProfile:
    source: str
    source_px: str
    target_cm: str
    print_px_at_dpi: str
    print_dpi: int
    gpt_image_size: str
    aspect_ratio: float
    dominant_palette: list[str]
    source_effective_dpi: float
    notes: list[str]


def slugify_filename(name: str) -> str:
    stem = Path(name).stem
    slug = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", stem, flags=re.UNICODE)
    return slug.strip("_") or "asset"


def round_down_to_multiple(value: float, multiple: int = 16) -> int:
    return max(multiple, int(value) // multiple * multiple)


def choose_gpt_image_size(width_cm: int, height_cm: int) -> tuple[int, int, list[str]]:
    ratio = width_cm / height_cm
    notes: list[str] = []

    if ratio > 3 or ratio < 1 / 3:
        notes.append(
            "Target aspect ratio exceeds gpt-image-2 3:1 limit; use a segmented or cropped master workflow."
        )
        ratio = min(3, max(1 / 3, ratio))

    height = (MAX_GPT_IMAGE_PIXELS / ratio) ** 0.5
    width = ratio * height

    if max(width, height) > MAX_GPT_IMAGE_EDGE:
        scale = MAX_GPT_IMAGE_EDGE / max(width, height)
        width *= scale
        height *= scale

    width_px = round_down_to_multiple(width)
    height_px = round_down_to_multiple(height)

    while width_px * height_px > MAX_GPT_IMAGE_PIXELS:
        if width_px >= height_px:
            width_px -= 16
        else:
            height_px -= 16

    if width_px * height_px < MIN_GPT_IMAGE_PIXELS:
        notes.append("Generated master size would be below gpt-image-2 minimum pixel count.")

    return width_px, height_px, notes


def dominant_palette(path: Path, colors: int = 8) -> list[str]:
    with Image.open(path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((220, 220), Image.Resampling.LANCZOS)
        quantized = image.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        counts = sorted(quantized.getcolors() or [], reverse=True)

    swatches: list[str] = []
    for _, index in counts[:colors]:
        offset = index * 3
        rgb = tuple(palette[offset : offset + 3])
        if len(rgb) == 3:
            swatches.append("#%02x%02x%02x" % rgb)
    return swatches


def save_preview(source: Path, destination: Path, max_edge: int = 1600) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        image.save(destination, quality=92, optimize=True)


# 文生图 / 整幅海报工作流的产物（master.png）文件名不带物理尺寸，
# 尺寸写在同目录 print_spec.json 与父目录名（如 ..._80x80_...）里。
# 串接这些产物做 GPT 重建时，需要在文件名解析失败后回退到这两处。
_DIR_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[乘xX×*]\s*(\d+(?:\.\d+)?)")


def _normalize_cm(value: float) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _size_from_print_spec(source: Path) -> tuple[float, float] | None:
    spec_path = source.parent / "print_spec.json"
    if not spec_path.is_file():
        return None
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        width_cm = float(spec["width_cm"])
        height_cm = float(spec["height_cm"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if width_cm <= 0 or height_cm <= 0:
        return None
    return width_cm, height_cm


def _size_from_ancestors(source: Path) -> tuple[float, float] | None:
    for part in reversed(source.parent.parts):
        match = _DIR_SIZE_RE.search(part)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None


def resolve_physical_size(source: Path) -> tuple[int | float, int | float] | None:
    """Resolve the target physical size (cm) for a rebuild source.

    Resolution order: size token in the filename (e.g. ``80乘80``) → sibling
    ``print_spec.json`` → ancestor directory name (e.g. ``..._80x80_...``).
    This lets chained assets such as the text-to-image ``master.png`` — whose
    filename carries no size — inherit the size their workflow already recorded.
    """
    for candidate in (
        parse_size_from_name(source),
        _size_from_print_spec(source),
        _size_from_ancestors(source),
    ):
        if candidate:
            width_cm, height_cm = candidate
            return _normalize_cm(width_cm), _normalize_cm(height_cm)
    return None


def resolve_size_override(
    width_cm: float | None,
    height_cm: float | None,
) -> tuple[int | float, int | float] | None:
    """Validate an explicit physical-size override from the caller.

    Returns ``None`` when neither dimension is given (fall back to inference),
    a normalized ``(width, height)`` tuple when both are valid, and raises
    ``ValueError`` when only one is given or a value is non-positive. Normalizing
    here keeps the tuple shape identical to ``resolve_physical_size`` (80.0 → 80).
    """
    if width_cm is None and height_cm is None:
        return None
    if width_cm is None or height_cm is None:
        raise ValueError("--width-cm 与 --height-cm 必须同时提供")
    if width_cm <= 0 or height_cm <= 0:
        raise ValueError("--width-cm/--height-cm 必须为正数")
    return _normalize_cm(width_cm), _normalize_cm(height_cm)


def build_profile(
    source: Path,
    print_dpi: int,
    size_override: tuple[int | float, int | float] | None = None,
) -> ImageProfile:
    # 显式 override 优先于文件名/print_spec/目录名推断（override 恒为非空二元组，短路安全）。
    size = size_override or resolve_physical_size(source)
    if not size:
        raise ValueError(
            "无法确定物理尺寸：源图文件名未包含尺寸标记（如 80乘80），"
            f"同目录也缺少可用的 print_spec.json：{source.name}"
        )

    width_cm, height_cm = size
    with Image.open(source) as image:
        source_width, source_height = image.size

    print_width, print_height = target_pixels(width_cm, height_cm, print_dpi)
    master_width, master_height, notes = choose_gpt_image_size(width_cm, height_cm)
    effective_dpi = min(
        source_width / (width_cm / 2.54),
        source_height / (height_cm / 2.54),
    )

    if effective_dpi < 80:
        notes.append(
            "Source is below 80 effective DPI; text/QR/logo details need deterministic reconstruction."
        )

    return ImageProfile(
        source=source.name,
        source_px=f"{source_width}x{source_height}",
        target_cm=f"{width_cm}x{height_cm}",
        print_px_at_dpi=f"{print_width}x{print_height}",
        print_dpi=print_dpi,
        gpt_image_size=f"{master_width}x{master_height}",
        aspect_ratio=round(width_cm / height_cm, 4),
        dominant_palette=dominant_palette(source),
        source_effective_dpi=round(effective_dpi, 1),
        notes=notes,
    )


# When the edit request is blank, description_text must not read as an
# instruction: state plainly that nothing should change, so the whole-image
# edit degrades to a faithful reproduction instead of an open-ended redraw.
_EDIT_NO_CHANGE_DESCRIPTION = (
    "No specific modification requested — reproduce the source faithfully, "
    "leaving every element and all text unchanged."
)


def build_visual_prompt(
    profile: ImageProfile,
    description: str | None,
    api_mode: str,
) -> str:
    palette = ", ".join(profile.dominant_palette)
    if api_mode == "edit":
        return _build_edit_prompt(profile, description, palette)
    return _build_generate_prompt(profile, description, palette)


def _build_edit_prompt(
    profile: ImageProfile,
    description: str | None,
    palette: str,
) -> str:
    # 图片修改（/images/edits，整图无 mask）：默认保留原图上的一切——尤其是文字、
    # 价格、logo、二维码——只应用用户"修改要求"里描述的改动。历史提示词曾指示模型
    # 抹除文字并重建为独立图层，导致带文字的图被自动去字；这里彻底反转为"保真编辑"。
    description_text = (description or "").strip() or _EDIT_NO_CHANGE_DESCRIPTION
    return textwrap.dedent(
        f"""
        Use case: ads-marketing
        Asset type: faithful in-place edit of the supplied print poster — the original poster with one requested change applied and every other element, above all all text, preserved unchanged.
        Primary request: Apply ONLY the single modification requested below to the source poster, and keep 100% of everything else pixel-identical to the source. This is a targeted, surgical edit of the existing image — NOT a rebuild, redraw, cleanup, restyle, re-typeset, or reinterpretation. Every word, number, price, logo, brand mark, small label, and QR code must stay exactly as it already appears unless the requested modification below explicitly targets it. If the requested modification below is empty or is only a generic reference note, make no content change at all: return a lossless, faithful reproduction of the source with all text and layout untouched.
        Source description: Requested change to apply — change only this, and leave everything else untouched: {description_text}
        Input/reference policy: Treat the input image as the exact base to edit in place, not as loose inspiration or a style reference. Start from its exact pixels and alter only the region(s) the requested change strictly requires; carry over every other region — the overall layout, the background outside the edited area, the main subject, all colors, and all text blocks — unchanged from the input. Do not regenerate, reimagine, redraw, or re-typeset the poster from scratch.
        Composition/framing: Preserve the source poster's exact composition, camera angle, main subject placement, visual hierarchy, whitespace blocks, copy-safe regions, and aspect ratio. Do not move, resize, crop, re-flow, mirror, or rearrange any zone except what the requested change strictly requires.
        Style/medium: Match the source's existing style, medium, lighting, texture, colors, and finish exactly; do not restyle the poster. Any new pixels introduced by the requested modification must blend seamlessly into the source's original look and stay confined to the area the modification targets. Do not "improve", denoise, sharpen, recolor, or relight anything that was not requested.
        Color palette: keep the source's existing colors exactly as they are, except where the requested modification itself requires a color change; dominant source colors for reference: {palette}.
        Text policy: PRESERVE EVERY PIECE OF TEXT EXACTLY AS IN THE SOURCE. Keep all Chinese and English words, headlines, prices, phone numbers, dates, logos, brand marks, small UI labels, legal/production copy, and every QR code fully legible and identical — same glyphs, characters, digits, spelling, font, weight, size, color, kerning, and position. Do NOT remove, hide, blur, cover, add, translate, rewrite, correct, re-typeset, restyle, re-render, or relocate any text or QR code, and do NOT replace text with empty panels, glows, frames, or texture. Reproduce every glyph pixel-faithfully. THE ONLY EXCEPTION: if the requested modification above explicitly asks to change, replace, or remove specific text, apply exactly that instruction to that specific text alone and leave all other text and QR codes untouched.
        Constraints: Make the smallest change that satisfies the request and change nothing the user did not explicitly request; when the requested modification is empty, change nothing at all and reproduce the source faithfully and losslessly. Above all, keep every glyph, digit, price, logo, brand mark, and QR code identical to the source unless the modification explicitly targets it. Keep the main subject position, visual hierarchy, aspect ratio, and all major layout regions unchanged; do not crop. Avoid distorted anatomy, garbled or invented typography, fake or altered logos, invented or altered QR codes, watermarks, signatures, or screenshots inside the image.
        Output: one complete whole-image edited poster at {profile.gpt_image_size}, faithful to the source with all original text and QR codes intact, no border, no margin.
        """
    ).strip()


def _build_generate_prompt(
    profile: ImageProfile,
    description: str | None,
    palette: str,
) -> str:
    # 文生图/CLI 重建路线：沿用"生成干净底图、文字与二维码作为独立生产图层重建"的
    # 策略，因此仍显式要求移除可读文字。GUI 恒定走 edit 分支，不经过此处。
    description_text = description or (
        "Use the supplied source preview as the visual reference for subject, layout, mood, and color."
    )
    return textwrap.dedent(
        f"""
        Use case: ads-marketing
        Asset type: clean image-model master for a large-format print poster
        Primary request: Rebuild the source poster as a high-quality polished illustration master for later print production.
        Source description: {description_text}
        Input/reference policy: Use the supplied source preview as the visual reference for subject, layout, mood, and color.
        Composition/framing: Preserve the source poster's overall composition, camera angle, main subject placement, visual hierarchy, whitespace blocks, and copy-safe regions. Do not crop away major zones from the original poster.
        Style/medium: premium commercial AI illustration, crisp details, clean lighting, high contrast, refined edges, suitable for large-format print.
        Color palette: keep close to these dominant source colors: {palette}.
        Text policy: remove readable Chinese and English text, prices, QR codes, logos, brand marks, small UI labels, and legal copy. Replace them with clean empty panels, glows, decorative frames, or unobtrusive texture in the same approximate locations so production text and QR code can be rebuilt as separate layers.
        Constraints: keep the scene visually faithful to the source; avoid changing the main subject position, visual hierarchy, aspect ratio, or major layout regions. Avoid distorted anatomy, fake logos, garbled typography, invented QR codes, watermarks, signatures, or screenshots inside the image.
        Output: one complete poster-background master at {profile.gpt_image_size}, no border, no margin.
        """
    ).strip()


def build_image_generation_payload(profile: ImageProfile, prompt: str) -> dict[str, Any]:
    return {
        "model": "gpt-image-2",
        "prompt": prompt,
        "size": profile.gpt_image_size,
        "quality": "high",
        "output_format": "png",
    }


def build_image_edit_payload(profile: ImageProfile, prompt: str) -> dict[str, Any]:
    return {
        "model": "gpt-image-2",
        "prompt": prompt,
        "size": profile.gpt_image_size,
        "quality": "high",
        "output_format": "png",
    }


def build_vision_prompt() -> str:
    return textwrap.dedent(
        """
        Analyze this poster image for a print-production rebuild workflow.
        Return concise JSON with:
        - scene_description: subject, setting, style, lighting, color palette
        - composition: main regions from top to bottom / left to right
        - text_layers: visible text copied as accurately as possible, with rough location and priority
        - logos_or_marks: visible brand/logo areas
        - qr_or_codes: whether QR/barcodes are present and approximate location
        - rebuild_prompt: a text-to-image prompt for generating only the clean background/illustration layer, excluding readable text, logo, QR code, prices, and small labels
        - production_notes: risks for print, OCR, QR, or brand reconstruction
        """
    ).strip()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_curl_script(
    path: Path,
    request_json_name: str,
    output_name: str,
    api_mode: str,
    source_name: str,
) -> None:
    if api_mode == "edit":
        script = textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail

            : "${{OPENAI_API_KEY:?Set OPENAI_API_KEY before running}}"
            OPENAI_BASE_URL="${{OPENAI_BASE_URL:-https://api.openai.com/v1}}"

            python3 - <<'PY'
            import base64
            import json
            import os
            import urllib.request
            import requests

            with open("{request_json_name}", "r", encoding="utf-8") as handle:
                payload = json.load(handle)

            with open("{source_name}", "rb") as image_file:
                response = requests.post(
                    os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/images/edits",
                    headers={{"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]}},
                    data=payload,
                    files={{"image": image_file}},
                    timeout=900,
                )
            response.raise_for_status()
            data = response.json()
            item = data["data"][0]
            if item.get("b64_json"):
                content = base64.b64decode(item["b64_json"])
            else:
                content = urllib.request.urlopen(item["url"], timeout=900).read()
            with open("{output_name}", "wb") as handle:
                handle.write(content)
            PY
            """
        )
        path.write_text(script, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return

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


def build_package(
    source: Path,
    output_dir: Path,
    print_dpi: int,
    description: str | None,
    execute: bool,
    api_mode: str,
    size_override: tuple[int | float, int | float] | None = None,
) -> Path:
    _stages = ["解析源图", "生成预览与请求包"]
    if execute:
        _stages.append("调用图像 API")
    _stages.append("写状态与完成")
    progress.plan(_stages)

    def _advance(label: str) -> None:
        if label in _stages:
            progress.stage(_stages.index(label) + 1)

    _advance("解析源图")
    profile = build_profile(source, print_dpi, size_override)
    package_dir = output_dir / f"{slugify_filename(source.name)}_{api_mode}"
    package_dir.mkdir(parents=True, exist_ok=True)

    _advance("生成预览与请求包")
    preview_path = package_dir / "source_preview.jpg"
    save_preview(source, preview_path)
    source_copy = package_dir / source.name
    if source_copy.resolve() != source.resolve():
        source_copy.write_bytes(source.read_bytes())

    prompt = build_visual_prompt(profile, description, api_mode)
    if api_mode == "edit":
        payload = build_image_edit_payload(profile, prompt)
        request_name = "image_edit_request.json"
        output_name = "gpt_image_edit_master.png"
    else:
        payload = build_image_generation_payload(profile, prompt)
        request_name = "image_generation_request.json"
        output_name = "gpt_image_master.png"

    (package_dir / "prompt.md").write_text(prompt + "\n", encoding="utf-8")
    write_json(package_dir / "profile.json", asdict(profile))
    write_json(package_dir / request_name, payload)
    write_json(
        package_dir / "vision_prompt_request.skeleton.json",
        {
            "model": "${OPENAI_PROMPT_MODEL:-gpt-5.5}",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": build_vision_prompt()},
                        {
                            "type": "input_image",
                            "image_url": "data:image/jpeg;base64,<base64 of source_preview.jpg>",
                        },
                    ],
                }
            ],
        },
    )
    write_curl_script(
        package_dir / "run_gpt_image_generation.sh",
        request_name,
        output_name,
        api_mode,
        source.name,
    )

    status = {
        "api_mode": api_mode,
        "image_generation": {"status": "prepared"},
        "text_qr_logo_rebuild": {
            "status": "not_implemented",
            "reason": "OCR/QR/logo extraction backend is not installed in this environment.",
        },
        "next_step_after_master": (
            "Run scripts/prepare_print_assets.py on gpt_image_master.png after naming/copying it with the target physical dimensions."
        ),
    }
    if execute:
        _advance("调用图像 API")
        status["image_generation"] = execute_image_request(
            source,
            payload,
            package_dir / output_name,
            api_mode,
        )
    elif not os.environ.get("OPENAI_API_KEY"):
        status["image_generation"] = {
            "status": "prepared_not_executed",
            "reason": "OPENAI_API_KEY is not set. Run run_gpt_image_generation.sh after configuring it.",
        }

    _advance("写状态与完成")
    write_json(package_dir / "status.json", status)

    if api_mode == "edit":
        workflow_intro = (
            "This package prepares an in-place image edit that keeps the source's existing text intact:"
        )
        step_rebuild = (
            "The edited master already preserves the original text, logo, price, and QR code — "
            "no separate rebuild layer is needed."
        )
        step_feed = "Feed the edited master into the print output stage."
        prompt_desc = "in-place edit prompt that preserves existing text."
    else:
        workflow_intro = "This package prepares the creative rebuild path:"
        step_rebuild = "Rebuild text, logo, price, and QR code as deterministic production layers."
        step_feed = "Feed the generated master and rebuilt layers into the print output stage."
        prompt_desc = "clean-background generation prompt."

    readme = textwrap.dedent(
        f"""
        # GPT Image Rebuild Package

        Source: `{source.name}`

        {workflow_intro}

        1. Use `source_preview.jpg` as the human/model reference.
        2. Review `prompt.md`.
        3. Run `./run_gpt_image_generation.sh` after setting `OPENAI_API_KEY`, or run this script again with `--execute`.
        4. {step_rebuild}
        5. {step_feed}

        Files:
        - `profile.json`: source/target metrics and selected GPT Image master size.
        - `prompt.md`: {prompt_desc}
        - `{request_name}`: Image API request fields for `gpt-image-2`.
        - `vision_prompt_request.skeleton.json`: prompt-extraction request skeleton for a vision-capable model.
        - `status.json`: current execution status.
        """
    ).strip()
    (package_dir / "README.md").write_text(readme + "\n", encoding="utf-8")

    progress.done(str(package_dir))
    return package_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Source image to rebuild.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("workflow_samples/gpt_image_rebuild"),
        help="Directory for generated workflow packages.",
    )
    parser.add_argument("--print-dpi", type=int, default=200)
    parser.add_argument(
        "--width-cm",
        type=float,
        default=None,
        help=(
            "目标物理宽度(cm)。需与 --height-cm 同时提供；两者都给出时覆盖"
            "文件名/print_spec/目录名推断。"
        ),
    )
    parser.add_argument(
        "--height-cm",
        type=float,
        default=None,
        help="目标物理高度(cm)。需与 --width-cm 同时提供。",
    )
    parser.add_argument(
        "--description",
        help="Optional human-authored description to seed the rebuild prompt.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Call the OpenAI Image API now. Requires OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--api-mode",
        choices=("generate", "edit"),
        default="generate",
        help="Use text-only generations or source-image edits for stronger composition preservation.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        size_override = resolve_size_override(args.width_cm, args.height_cm)
    except ValueError as exc:
        parser.error(str(exc))
    package_dir = build_package(
        args.source.resolve(),
        args.output_dir.resolve(),
        args.print_dpi,
        args.description,
        args.execute,
        args.api_mode,
        size_override,
    )
    print(f"Package written to {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
