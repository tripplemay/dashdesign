#!/usr/bin/env python3
"""Generate complete poster candidates with image-model rendered typography."""

from __future__ import annotations

import argparse
import stat
import sys
import textwrap
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from image_api_client import execute_image_generation
from prompt_template_compiler import (
    DEFAULT_TEMPLATE_LIBRARY,
    compile_prompt_template_profile,
    load_template_library,
)
from text_to_image_print import (
    DEFAULT_BASELINE,
    PosterCopy,
    blocked_terms_in_prompt,
    build_payload,
    cm_label,
    generated_image_audit,
    load_baseline,
    parse_poster_copy,
    prepare_print_output,
    profile_hash,
    prompt_context_source,
    resolve_image_size,
    target_pixels,
    write_json,
)


PROMPT_TEMPLATE_VERSION = "full_poster_image2.v2"
DEFAULT_FULL_POSTER_OUTPUT_DIR = Path("workflow_samples") / "full_poster_image2"


def exact_copy_lines(poster_copy: PosterCopy) -> list[str]:
    lines: list[str] = []
    if poster_copy.headline:
        lines.append(f'主标题："{poster_copy.headline}"')
    if poster_copy.subtitle:
        lines.append(f'副标题："{poster_copy.subtitle}"')
    for index, module in enumerate(poster_copy.normalized_modules(), start=1):
        lines.append(f'课程模块{index}："{module}"')
    if poster_copy.cta:
        lines.append(f'行动号召："{poster_copy.cta}"')
    return lines


def expected_text_items(poster_copy: PosterCopy) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if poster_copy.headline:
        items.append({"role": "headline", "text": poster_copy.headline})
    if poster_copy.subtitle:
        items.append({"role": "subtitle", "text": poster_copy.subtitle})
    for index, module in enumerate(poster_copy.normalized_modules(), start=1):
        items.append({"role": f"module_{index}", "text": module})
    if poster_copy.cta:
        items.append({"role": "cta", "text": poster_copy.cta})
    return items


def text_density_guidance(poster_copy: PosterCopy) -> list[str]:
    modules = poster_copy.normalized_modules()
    guidance = [
        "Use fewer, larger text areas instead of many tiny labels.",
        "Keep all required Chinese text large, legible, and high contrast.",
    ]
    if len(modules) >= 4:
        guidance.append(
            "Course modules may use compact badge titles; keep supporting details minimal and readable."
        )
    if len(poster_copy.subtitle) > 34:
        guidance.append(
            "The subtitle is long; render it as one clean subtitle band or two balanced lines."
        )
    return guidance


def build_full_poster_prompt(
    baseline: dict[str, Any],
    user_prompt: str,
    poster_copy: PosterCopy,
    width_cm: float,
    height_cm: float,
    dpi: int,
    image_size: str,
    style: str,
    template_profile: dict[str, Any],
) -> str:
    project = baseline.get("project", {}) if isinstance(baseline, dict) else {}
    consumer = baseline.get("consumer_baseline", {}) if isinstance(baseline, dict) else {}
    visual = baseline.get("visual_guidelines", {}) if isinstance(baseline, dict) else {}
    audience = consumer.get("audience", {}) if isinstance(consumer, dict) else {}

    modules = poster_copy.normalized_modules()
    template_labels = template_profile.get("labels", {}) if isinstance(template_profile, dict) else {}
    template_ids = template_profile.get("ids", {}) if isinstance(template_profile, dict) else {}
    positive_blocks = template_profile.get("positive_blocks", []) if isinstance(template_profile, dict) else []
    typography_blocks = template_profile.get("typography_blocks", []) if isinstance(template_profile, dict) else []
    cta_guidance = template_profile.get("cta_guidance", []) if isinstance(template_profile, dict) else []
    negative_blocks = template_profile.get("negative_blocks", []) if isinstance(template_profile, dict) else []
    risk_notes = template_profile.get("risk_notes", []) if isinstance(template_profile, dict) else []
    sections = [
        "Task: Generate one complete Chinese enrollment poster as a finished raster image.",
        "The image model must design the background, composition, typography, title effects, course badges, and call-to-action together.",
        "This is not a background-only request. Render the required Chinese poster text directly inside the image.",
        "",
        f"Project: {project.get('name', 'AI digital creation course')}",
        f"Target audience mode: {baseline.get('target_audience_mode', 'to_c_parent_student')}",
        f"Primary decision maker: {audience.get('primary_decision_maker', 'parents')}",
        f"End user: {audience.get('end_user', 'children and teenagers')}",
        "",
        "Consumer positioning:",
        str(consumer.get("positioning", "")),
        "",
        "Prompt template selections:",
        f"- Purpose: {template_labels.get('purpose', '')} ({template_ids.get('purpose', '')})",
        f"- Style: {template_labels.get('style', '')} ({template_ids.get('style', '')})",
        f"- Layout: {template_labels.get('layout', '')} ({template_ids.get('layout', '')})",
        f"- Text density: {template_labels.get('text_density', '')} ({template_ids.get('text_density', '')})",
        "",
        "Template creative brief:",
        *[f"- {item}" for item in positive_blocks],
        "",
        "Visual brief:",
        user_prompt,
        "",
        "Additional user art direction:",
        style,
        "",
        "Required exact Chinese copy. Render these strings accurately. Do not rewrite, translate, abbreviate, or add extra words:",
        *[f"- {line}" for line in exact_copy_lines(poster_copy)],
        "",
        "Typography requirements:",
        *[f"- {item}" for item in typography_blocks],
        "- Main headline must look like custom professional Chinese poster lettering, not a plain system font.",
        "- Use strong title hierarchy, designed strokes, dimensional outline, glow or material effects that match the scene.",
        "- Course modules should look like poster badges, stickers, futuristic labels, or integrated display panels, not generic software UI cards.",
        "- The call-to-action should feel like a promotional poster ribbon or banner.",
        "- Ensure Chinese glyphs are complete, readable, and visually balanced.",
        *[f"- {item}" for item in text_density_guidance(poster_copy)],
        *[f"- {item}" for item in cta_guidance],
        *[f"- Template risk note: {item}" for item in risk_notes],
        "",
        "Scene and subject guidance from project baseline:",
        *[f"- {item}" for item in visual.get("style_keywords", [])],
        *[f"- {item}" for item in visual.get("recommended_subjects", [])[:5]],
        *[f"- {item}" for item in visual.get("recommended_scenes", [])[:5]],
        "",
        "Course capabilities to express visually:",
        *[f"- {item}" for item in consumer.get("course_modules", [])],
        "",
        "Production constraints:",
        f"- Physical target after post-processing: {cm_label(width_cm)}cm x {cm_label(height_cm)}cm at {dpi} DPI.",
        f"- Image API master size: {image_size}.",
        "- Leave a clean placeholder area for a real QR code, but do not generate a scannable QR code.",
        "- Do not include phone numbers, prices, logos, watermarks, signatures, business partnership language, or school-operator scenes.",
        "- Do not include pseudo-text, gibberish, misspelled Chinese, duplicate text, or any extra readable text beyond the required copy.",
        *[f"- {item}" for item in negative_blocks],
    ]
    if modules:
        sections.append("- Keep module text grouped in a coherent course section.")
    return "\n".join(part for part in sections if part is not None).strip() + "\n"


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


def build_package(
    baseline_path: Path,
    template_library_path: Path,
    output_dir: Path,
    width_cm: float,
    height_cm: float,
    dpi: int,
    user_prompt: str,
    poster_copy_text: str,
    style: str,
    purpose_template: str | None,
    style_template: str | None,
    layout_template: str | None,
    text_density_template: str | None,
    negative_template: str | None,
    model: str,
    quality: str,
    requested_image_size: str,
    output_format: str,
    candidates: int,
    execute: bool,
    postprocess_print: bool,
    allow_blocked_terms: bool,
) -> Path:
    if width_cm <= 0 or height_cm <= 0:
        raise ValueError("Width and height must be positive centimeters")
    if dpi <= 0:
        raise ValueError("DPI must be positive")
    if candidates <= 0:
        raise ValueError("Candidates must be positive")
    if not user_prompt.strip():
        user_prompt = (
            "Use the selected purpose, style, layout, text-density template, "
            "project baseline, and poster copy to design a complete to-C AI education poster."
        )
    if not poster_copy_text.strip():
        raise ValueError("Full-poster mode requires poster copy")

    baseline = load_baseline(baseline_path)
    template_library = load_template_library(template_library_path)
    template_profile = compile_prompt_template_profile(
        template_library,
        purpose_template,
        style_template,
        layout_template,
        text_density_template,
        negative_template,
    )
    poster_copy = parse_poster_copy(poster_copy_text)
    if not poster_copy.has_content():
        raise ValueError("Poster copy did not include usable headline/module/CTA content")

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
        raise ValueError(f"整图海报提示词包含当前 C 端基线禁用词：{terms}")

    image_size = resolve_image_size(width_cm, height_cm, requested_image_size)
    prompt = build_full_poster_prompt(
        baseline,
        user_prompt,
        poster_copy,
        width_cm,
        height_cm,
        dpi,
        image_size,
        style,
        template_profile,
    )
    context_source = prompt_context_source(baseline)
    context_hash = profile_hash(context_source)
    copy_hash = profile_hash(asdict(poster_copy))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    package_dir = output_dir / f"{timestamp}_{cm_label(width_cm)}x{cm_label(height_cm)}_full_poster_image2"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    write_json(package_dir / "baseline_context.json", context_source)
    write_json(package_dir / "prompt_template_profile.json", template_profile)
    write_json(package_dir / "poster_copy.json", asdict(poster_copy))
    write_json(package_dir / "expected_text.json", expected_text_items(poster_copy))
    write_json(
        package_dir / "print_spec.json",
        {
            "width_cm": width_cm,
            "height_cm": height_cm,
            "dpi": dpi,
            "target_px": "%sx%s" % target_pixels(width_cm, height_cm, dpi),
            "image_api_master_size": image_size,
            "output_format": output_format,
            "workflow": "full_poster_image2",
        },
    )
    write_json(
        package_dir / "generation_record.json",
        {
            "baseline_id": baseline.get("baseline_id"),
            "baseline_version": baseline.get("version"),
            "target_audience_mode": baseline.get("target_audience_mode"),
            "profile_hash": context_hash,
            "poster_copy_hash": copy_hash,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "prompt_template_library": str(template_library_path),
            "prompt_template_library_version": template_profile.get("library_version"),
            "prompt_template_ids": template_profile.get("ids"),
            "workflow": "full_poster_image2",
            "model": model,
            "size": image_size,
            "quality": quality,
            "candidates": candidates,
            "execute_requested": execute,
            "postprocess_print_requested": postprocess_print,
        },
    )

    status: dict[str, Any] = {
        "workflow": "full_poster_image2",
        "image_generation": [],
        "print_output": [],
        "blocked_prompt_terms": blocked_terms,
        "ocr_evaluation": {
            "status": "manual_required",
            "reason": "No OCR engine is configured in this environment.",
            "expected_text_file": "expected_text.json",
        },
    }

    for index in range(1, candidates + 1):
        candidate_dir = package_dir / f"candidate_{index:02d}"
        candidate_dir.mkdir(parents=True, exist_ok=True)
        payload = build_payload(model, prompt, image_size, quality, output_format)
        request_name = "image_generation_request.json"
        master_name = f"full_poster_master.{output_format.replace('jpeg', 'jpg')}"
        write_json(candidate_dir / request_name, payload)
        write_run_script(candidate_dir / "run_full_poster_generation.sh", request_name, master_name)

        candidate_status: dict[str, Any] = {
            "candidate": index,
            "status": "prepared",
            "request": str(candidate_dir / request_name),
            "master": str(candidate_dir / master_name),
        }
        if execute:
            result = execute_image_generation(payload, candidate_dir / master_name)
            if isinstance(result, dict) and result.get("status") == "generated":
                result.update(generated_image_audit(candidate_dir / master_name, image_size, width_cm, height_cm))
            candidate_status.update(result)
        else:
            candidate_status.update(
                {
                    "status": "prepared_not_executed",
                    "reason": "Run with --execute or use candidate run script after configuring OPENAI_API_KEY.",
                }
            )
        status["image_generation"].append(candidate_status)

        if postprocess_print:
            print_status: dict[str, Any] = {"candidate": index, "status": "pending_image_generation"}
            if candidate_status.get("status") != "generated":
                print_status = {
                    "candidate": index,
                    "status": "skipped",
                    "reason": "master image was not generated",
                }
            elif not candidate_status.get("orientation_matches_target"):
                print_status = {
                    "candidate": index,
                    "status": "skipped",
                    "reason": "generated master orientation does not match target print orientation",
                    "actual_orientation": candidate_status.get("actual_orientation"),
                    "target_orientation": candidate_status.get("target_orientation"),
                }
            else:
                print_output = candidate_dir / "print_ready" / (
                    f"{cm_label(width_cm)}乘以{cm_label(height_cm)}_整图海报候选{index:02d}.jpg"
                )
                print_status = {"candidate": index, **prepare_print_output(candidate_dir / master_name, print_output, width_cm, height_cm, dpi)}
            status["print_output"].append(print_status)

    write_json(package_dir / "status.json", status)
    readme = textwrap.dedent(
        f"""
        # Full Poster Image2 Package

        This package evaluates a complete-poster generation route where
        `gpt-image-2` renders the background, Chinese typography, title effects,
        course section, and call-to-action together.

        Review priority:
        1. Compare each image against `expected_text.json`.
        2. Reject candidates with wrong, missing, duplicated, or extra text.
        3. Reject fake QR codes; only a clean QR placeholder is acceptable.
        4. Prefer the candidate with the best integrated poster typography and
           least generic UI-card feel.
        """
    ).strip()
    (package_dir / "README.md").write_text(readme + "\n", encoding="utf-8")
    return package_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--template-library", type=Path, default=DEFAULT_TEMPLATE_LIBRARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_FULL_POSTER_OUTPUT_DIR)
    parser.add_argument("--width-cm", type=float, required=True)
    parser.add_argument("--height-cm", type=float, required=True)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--prompt", default="", help="Optional visual brief for the complete poster.")
    parser.add_argument("--poster-copy", required=True, help="Raw Chinese poster copy to render inside the image.")
    parser.add_argument(
        "--style",
        default="high-end AI education enrollment poster, cinematic neon lighting, professional Chinese advertising typography",
        help="Typography and art-direction style.",
    )
    parser.add_argument("--purpose-template", default=None, help="Purpose template id.")
    parser.add_argument("--style-template", default=None, help="Style template id.")
    parser.add_argument("--layout-template", default=None, help="Layout template id.")
    parser.add_argument("--text-density", default=None, help="Text-density template id.")
    parser.add_argument("--negative-template", default=None, help="Negative prompt template id.")
    parser.add_argument("--model", default="gpt-image-2")
    parser.add_argument("--quality", default="high", choices=("low", "medium", "high", "auto"))
    parser.add_argument("--image-size", default="auto", help="auto or WIDTHxHEIGHT.")
    parser.add_argument("--output-format", default="png", choices=("png", "jpeg", "webp"))
    parser.add_argument("--candidates", type=int, default=4)
    parser.add_argument("--execute", action="store_true", help="Call the Image API now.")
    parser.add_argument("--postprocess-print", action="store_true", help="Resize generated candidates to print pixels.")
    parser.add_argument("--allow-blocked-terms", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        package_dir = build_package(
            args.baseline.resolve(),
            args.template_library.resolve(),
            args.output_dir.resolve(),
            args.width_cm,
            args.height_cm,
            args.dpi,
            args.prompt.strip(),
            args.poster_copy,
            args.style.strip(),
            args.purpose_template,
            args.style_template,
            args.layout_template,
            args.text_density,
            args.negative_template,
            args.model,
            args.quality,
            args.image_size,
            args.output_format,
            args.candidates,
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
