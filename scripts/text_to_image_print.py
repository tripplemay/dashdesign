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
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from image_api_client import execute_image_generation
from prepare_print_assets import (
    aspect_delta_percent,
    enhance,
    fit_with_blurred_background,
    save_print_image,
    target_pixels,
)


PROMPT_TEMPLATE_VERSION = "text_to_image_print.v1"
DEFAULT_BASELINE = Path("docs") / "baseline" / "baseline.v1.draft.json"
DEFAULT_OUTPUT_DIR = Path("workflow_samples") / "text_to_image_print"
IMAGE_SIZE_RE = re.compile(r"^\d+x\d+$")


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


def build_baseline_prompt_context(
    baseline: dict[str, Any],
    user_prompt: str,
    width_cm: float,
    height_cm: float,
    dpi: int,
    image_size: str,
) -> str:
    project = baseline.get("project", {}) if isinstance(baseline, dict) else {}
    consumer = baseline.get("consumer_baseline", {}) if isinstance(baseline, dict) else {}
    visual = baseline.get("visual_guidelines", {}) if isinstance(baseline, dict) else {}
    prompt_policy = baseline.get("prompt_policy", {}) if isinstance(baseline, dict) else {}
    audience = consumer.get("audience", {}) if isinstance(consumer, dict) else {}

    sections = [
        "Task: Generate one polished image-only poster background master for a to-C enrollment poster.",
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
        "User current request:",
        user_prompt,
        "",
        "Production constraints:",
        f"- Physical target: {cm_label(width_cm)}cm x {cm_label(height_cm)}cm at {dpi} DPI after print post-processing.",
        f"- Image API master size: {image_size}.",
        "- Generate the background/master artwork only. Leave clean safe areas for headline, course modules, call-to-action, and QR code.",
        "- Do not place final marketing copy, phone numbers, prices, logos, QR codes, watermarks, or signatures in the image.",
        "- If interface panels or module cards appear, use abstract marks and blank glow panels instead of readable text.",
        "- The visual must feel suitable for parents and children, not for school operators or business partners.",
        "",
        "Negative constraints:",
        *[f"- {item}" for item in prompt_policy.get("negative_constraints", [])],
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


def prepare_print_output(
    master_path: Path,
    output_path: Path,
    width_cm: float,
    height_cm: float,
    dpi: int,
) -> dict[str, Any]:
    target_size = target_pixels(width_cm, height_cm, dpi)
    with Image.open(master_path) as raw:
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
    return {
        "status": "generated",
        "output": str(output_path),
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
    allow_blocked_terms: bool,
) -> Path:
    if width_cm <= 0 or height_cm <= 0:
        raise ValueError("Width and height must be positive centimeters")
    if dpi <= 0:
        raise ValueError("DPI must be positive")
    if not user_prompt.strip():
        raise ValueError("Prompt must not be empty")

    baseline = load_baseline(baseline_path)
    blocked_terms = blocked_terms_in_prompt(user_prompt, baseline)
    if blocked_terms and not allow_blocked_terms:
        terms = "、".join(blocked_terms)
        raise ValueError(f"文生图提示词包含当前 C 端基线禁用词：{terms}")

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
    )
    payload = build_payload(model, prompt, image_size, quality, output_format)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = output_dir / f"{timestamp}_{cm_label(width_cm)}x{cm_label(height_cm)}_t2i"
    package_dir.mkdir(parents=True, exist_ok=True)

    master_name = f"master.{output_suffix(output_format)}"
    print_output = package_dir / "print_ready" / (
        f"{cm_label(width_cm)}乘以{cm_label(height_cm)}_文生图.jpg"
    )

    (package_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(package_dir / "baseline_context.json", context_source)
    write_json(package_dir / "image_generation_request.json", payload)
    write_json(
        package_dir / "print_spec.json",
        {
            "width_cm": width_cm,
            "height_cm": height_cm,
            "dpi": dpi,
            "target_px": "%sx%s" % target_pixels(width_cm, height_cm, dpi),
            "image_api_master_size": image_size,
            "output_format": output_format,
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
            "model": model,
            "size": image_size,
            "quality": quality,
            "execute_requested": execute,
            "postprocess_print_requested": postprocess_print,
        },
    )
    write_run_script(package_dir / "run_text_to_image_generation.sh", "image_generation_request.json", master_name)

    status: dict[str, Any] = {
        "image_generation": {"status": "prepared"},
        "print_output": {"status": "not_requested" if not postprocess_print else "pending_image_generation"},
        "blocked_prompt_terms": blocked_terms,
    }
    if execute:
        status["image_generation"] = execute_image_generation(payload, package_dir / master_name)
        if (
            postprocess_print
            and isinstance(status["image_generation"], dict)
            and status["image_generation"].get("status") == "generated"
        ):
            status["print_output"] = prepare_print_output(
                package_dir / master_name,
                print_output,
                width_cm,
                height_cm,
                dpi,
            )
        elif postprocess_print:
            status["print_output"] = {
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

    write_json(package_dir / "status.json", status)
    readme = textwrap.dedent(
        f"""
        # Baseline Text-to-Image Package

        This package generates a to-C poster background from the current project
        baseline and a user prompt.

        Files:
        - `prompt.md`: final baseline-aware image prompt.
        - `baseline_context.json`: exact baseline fields injected into the prompt.
        - `image_generation_request.json`: Image API request payload.
        - `generation_record.json`: baseline/model/profile metadata for traceability.
        - `status.json`: execution result.

        The prompt intentionally asks the image model not to create readable text,
        logos, QR codes, phone numbers, prices, or final copy. Those production
        layers should remain controlled outside the image model.
        """
    ).strip()
    (package_dir / "README.md").write_text(readme + "\n", encoding="utf-8")
    return package_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--width-cm", type=float, required=True)
    parser.add_argument("--height-cm", type=float, required=True)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--prompt", required=True, help="Current text-to-image request.")
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
            args.allow_blocked_terms,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Package written to {package_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
