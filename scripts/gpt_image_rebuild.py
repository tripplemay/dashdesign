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
import base64
import json
import os
import re
import stat
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps

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


def build_profile(source: Path, print_dpi: int) -> ImageProfile:
    size = parse_size_from_name(source)
    if not size:
        raise ValueError(f"Could not parse physical size from filename: {source.name}")

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


def build_visual_prompt(
    profile: ImageProfile,
    description: str | None,
    api_mode: str,
) -> str:
    description_text = description or (
        "Use the supplied source preview as the visual reference for subject, layout, mood, and color."
    )
    palette = ", ".join(profile.dominant_palette)
    source_reference = (
        "Use the input image as the primary composition reference. Match its spatial map closely: "
        "large title zone at the top, main character and magical drawing scene in the center, "
        "decorative feature/icon areas around the middle, and a clean lower zone for production copy."
        if api_mode == "edit"
        else "Use the supplied source preview as the visual reference for subject, layout, mood, and color."
    )

    return textwrap.dedent(
        f"""
        Use case: ads-marketing
        Asset type: clean image-model master for a large-format print poster
        Primary request: Rebuild the source poster as a high-quality polished illustration master for later print production.
        Source description: {description_text}
        Input/reference policy: {source_reference}
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


def execute_generation(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "status": "skipped",
            "reason": "OPENAI_API_KEY is not set",
        }

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    response = requests.post(
        f"{base_url}/images/generations",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=900,
    )
    if response.status_code >= 400:
        return {
            "status": "error",
            "status_code": response.status_code,
            "body": response.text[:2000],
        }

    data = response.json()
    first_image = data["data"][0]
    if first_image.get("b64_json"):
        output_path.write_bytes(base64.b64decode(first_image["b64_json"]))
    elif first_image.get("url"):
        image_response = requests.get(first_image["url"], timeout=900)
        image_response.raise_for_status()
        output_path.write_bytes(image_response.content)
    else:
        return {
            "status": "error",
            "reason": "Image response did not include b64_json or url",
            "body": json.dumps(data, ensure_ascii=False)[:2000],
        }
    return {
        "status": "generated",
        "output": str(output_path),
    }


def _write_api_image_response(data: dict[str, Any], output_path: Path) -> dict[str, Any]:
    first_image = data["data"][0]
    if first_image.get("b64_json"):
        output_path.write_bytes(base64.b64decode(first_image["b64_json"]))
    elif first_image.get("url"):
        image_response = requests.get(first_image["url"], timeout=900)
        image_response.raise_for_status()
        output_path.write_bytes(image_response.content)
    else:
        return {
            "status": "error",
            "reason": "Image response did not include b64_json or url",
            "body": json.dumps(data, ensure_ascii=False)[:2000],
        }

    with Image.open(output_path) as image:
        width, height = image.size
    return {
        "status": "generated",
        "output": str(output_path),
        "actual_px": f"{width}x{height}",
    }


def execute_image_request(
    source: Path,
    payload: dict[str, Any],
    output_path: Path,
    api_mode: str,
) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {
            "status": "skipped",
            "reason": "OPENAI_API_KEY is not set",
        }

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {api_key}"}
    if api_mode == "edit":
        with source.open("rb") as image_file:
            response = requests.post(
                f"{base_url}/images/edits",
                headers=headers,
                data=payload,
                files={"image": image_file},
                timeout=900,
            )
    else:
        response = requests.post(
            f"{base_url}/images/generations",
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
            timeout=900,
        )

    if response.status_code >= 400:
        return {
            "status": "error",
            "status_code": response.status_code,
            "body": response.text[:2000],
        }

    return _write_api_image_response(response.json(), output_path)


def build_package(
    source: Path,
    output_dir: Path,
    print_dpi: int,
    description: str | None,
    execute: bool,
    api_mode: str,
) -> Path:
    profile = build_profile(source, print_dpi)
    package_dir = output_dir / f"{slugify_filename(source.name)}_{api_mode}"
    package_dir.mkdir(parents=True, exist_ok=True)

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

    write_json(package_dir / "status.json", status)

    readme = textwrap.dedent(
        f"""
        # GPT Image Rebuild Package

        Source: `{source.name}`

        This package prepares the creative rebuild path:

        1. Use `source_preview.jpg` as the human/model reference.
        2. Review `prompt.md`.
        3. Run `./run_gpt_image_generation.sh` after setting `OPENAI_API_KEY`, or run this script again with `--execute`.
        4. Rebuild text, logo, price, and QR code as deterministic production layers.
        5. Feed the generated master and rebuilt layers into the print output stage.

        Files:
        - `profile.json`: source/target metrics and selected GPT Image master size.
        - `prompt.md`: clean-background generation prompt.
        - `{request_name}`: Image API request fields for `gpt-image-2`.
        - `vision_prompt_request.skeleton.json`: prompt-extraction request skeleton for a vision-capable model.
        - `status.json`: current execution status.
        """
    ).strip()
    (package_dir / "README.md").write_text(readme + "\n", encoding="utf-8")

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
    args = build_parser().parse_args()
    package_dir = build_package(
        args.source.resolve(),
        args.output_dir.resolve(),
        args.print_dpi,
        args.description,
        args.execute,
        args.api_mode,
    )
    print(f"Package written to {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
