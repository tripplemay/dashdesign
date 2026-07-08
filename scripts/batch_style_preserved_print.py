#!/usr/bin/env python3
"""Batch-build style-preserved 200dpi print images.

This workflow preserves original text, QR-code positions, logos, and layout.
It does not rebuild or move QR codes. Images that are small enough are first
enhanced with Real-ESRGAN; oversized sources are normalized directly to the
print target size to avoid impractical 4x masters.
"""

from __future__ import annotations

import argparse
import csv
import gc
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

import progress

from prepare_print_assets import (
    IMAGE_EXTENSIONS,
    SourceSpec,
    aspect_delta_percent,
    discover_sources,
    effective_dpi,
    enhance,
    save_print_image,
    target_pixels,
)


Image.MAX_IMAGE_PIXELS = None


def should_use_realesrgan(spec: SourceSpec, args: argparse.Namespace) -> bool:
    source_pixels = spec.source_width * spec.source_height
    if source_pixels > args.max_sr_input_pixels:
        return False
    if max(spec.source_width, spec.source_height) > args.max_sr_input_edge:
        return False
    if effective_dpi(spec) >= args.sr_dpi_threshold:
        return False
    return True


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
        args.thread_counts,
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


def fit_to_print_size(
    image: Image.Image,
    target_size: tuple[int, int],
    aspect_tolerance: float,
) -> tuple[Image.Image, str, tuple[int, int]]:
    delta = aspect_delta_percent(image.size, target_size)
    if abs(delta) <= aspect_tolerance:
        return image.resize(target_size, Image.Resampling.LANCZOS), "resized", target_size

    target_width, target_height = target_size
    background = ImageOps.fit(
        image,
        target_size,
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    blur_radius = max(18, int(max(target_size) * 0.018))
    background = background.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    background = ImageEnhance.Contrast(background).enhance(0.82)
    background = ImageEnhance.Brightness(background).enhance(0.86)

    scale = min(target_width / image.width, target_height / image.height)
    content_size = (
        max(1, int(round(image.width * scale))),
        max(1, int(round(image.height * scale))),
    )
    content = image.resize(content_size, Image.Resampling.LANCZOS)
    x = (target_width - content_size[0]) // 2
    y = (target_height - content_size[1]) // 2
    background.paste(content, (x, y))
    content.close()
    return background, "centered_with_blurred_background", content_size


def final_polish(image: Image.Image, backend: str) -> Image.Image:
    if backend == "realesrgan_x4plus":
        image = ImageEnhance.Contrast(image).enhance(1.012)
        image = ImageEnhance.Color(image).enhance(1.008)
        return image.filter(ImageFilter.UnsharpMask(radius=0.55, percent=55, threshold=3))
    return enhance(image)


def open_rgb(path: Path) -> tuple[Image.Image, bytes | None]:
    with Image.open(path) as raw:
        icc_profile = raw.info.get("icc_profile")
        image = ImageOps.exif_transpose(raw).convert("RGB")
    return image, icc_profile


def save_review(image: Image.Image, destination: Path, max_edge: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    review = image.copy()
    review.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    review.convert("RGB").save(destination, quality=90, optimize=True)
    review.close()


def build_contact_sheet(review_paths: list[Path], destination: Path) -> None:
    if not review_paths:
        return

    tile_w = 320
    tile_h = 300
    label_h = 50
    cols = 5
    rows = (len(review_paths) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), "white")
    draw = ImageDrawCompat(sheet)

    for index, path in enumerate(review_paths):
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
        image.thumbnail((tile_w, tile_h - label_h), Image.Resampling.LANCZOS)
        x0 = (index % cols) * tile_w
        y0 = (index // cols) * tile_h
        sheet.paste(image, (x0 + (tile_w - image.width) // 2, y0))
        draw.text((x0 + 8, y0 + tile_h - label_h + 8), path.name, fill=(0, 0, 0))
        image.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, quality=90, optimize=True)
    sheet.close()


class ImageDrawCompat:
    def __init__(self, image: Image.Image) -> None:
        from PIL import ImageDraw

        self._draw = ImageDraw.Draw(image)

    def text(self, xy: tuple[int, int], text: str, fill: tuple[int, int, int]) -> None:
        self._draw.text(xy, text, fill=fill)


def process_one(spec: SourceSpec, args: argparse.Namespace) -> dict[str, str | int | float]:
    target_size = target_pixels(spec.width_cm, spec.height_cm, args.dpi)
    use_sr = should_use_realesrgan(spec, args)
    backend = "realesrgan_x4plus" if use_sr else "pil_style_preserved"
    output_path = args.output_dir / spec.path.name
    review_path = args.output_dir / "review" / spec.path.with_suffix(".jpg").name
    master_path = args.output_dir / "_masters" / f"{spec.path.stem}_realesrgan_x4.png"

    with Image.open(spec.path) as raw_source:
        original_icc_profile = raw_source.info.get("icc_profile")

    if use_sr:
        run_realesrgan(spec.path, master_path, args)
        image, _ = open_rgb(master_path)
        icc_profile = original_icc_profile
    else:
        image, icc_profile = open_rgb(spec.path)

    prepared, layout, content_size = fit_to_print_size(image, target_size, args.aspect_tolerance)
    polished = final_polish(prepared, backend)
    save_print_image(polished, output_path, args.dpi, icc_profile)
    save_review(polished, review_path, args.review_max_edge)

    row: dict[str, str | int | float] = {
        "source": spec.path.name,
        "output": str(output_path),
        "review": str(review_path),
        "target_cm": f"{spec.width_cm}x{spec.height_cm}",
        "source_px": f"{spec.source_width}x{spec.source_height}",
        "output_px": f"{target_size[0]}x{target_size[1]}",
        "content_px": f"{content_size[0]}x{content_size[1]}",
        "source_effective_dpi": round(effective_dpi(spec), 1),
        "target_dpi": args.dpi,
        "backend": backend,
        "qr_rebuilt": "no",
        "text_rebuilt": "no",
        "layout": layout,
        "aspect_delta_percent": round(aspect_delta_percent((spec.source_width, spec.source_height), target_size), 2),
    }

    image.close()
    prepared.close()
    polished.close()
    if use_sr and not args.keep_masters:
        master_path.unlink(missing_ok=True)
    gc.collect()
    return row


def write_report(rows: list[dict[str, str | int | float]], output_dir: Path, dpi: int) -> None:
    csv_path = output_dir / "print_audit.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    sr_count = sum(1 for row in rows if row["backend"] == "realesrgan_x4plus")
    fallback_count = len(rows) - sr_count
    ratio_adjusted = sum(1 for row in rows if row["layout"] == "centered_with_blurred_background")

    lines = [
        "# Style-Preserved Print Batch",
        "",
        f"Processed images: {len(rows)}",
        f"Target DPI: {dpi}",
        f"Real-ESRGAN enhanced images: {sr_count}",
        f"PIL style-preserved fallback images: {fallback_count}",
        f"Aspect-ratio adjusted images: {ratio_adjusted}",
        "",
        "Workflow:",
        "",
        "1. Preserve the original image layout, typography, logo positions, and QR-code positions.",
        "2. Use Real-ESRGAN `x4plus` for sources that are small enough and below the DPI threshold.",
        "3. Fit each image to the print pixel size parsed from its filename.",
        "4. Apply light final contrast/color/sharpness polish.",
        "",
        "Important:",
        "",
        "- QR codes are not rebuilt, decoded, moved, or replaced in this batch.",
        "- Text is not rebuilt; the original font, gradient, stroke, shadow, and generated text style are preserved.",
        "- Already huge sources are not sent through 4x Real-ESRGAN to avoid impractical memory and output size.",
        "",
        "Files:",
        "",
        "- Full 200dpi print files: this directory.",
        "- Review previews: `review/*.jpg`.",
        "- Contact sheet: `review/contact_sheet.jpg`.",
        "- Audit table: `print_audit.csv`.",
        "",
    ]
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("print_ready_v3_style_preserved_no_qr_rebuild_200dpi"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--aspect-tolerance", type=float, default=0.5)
    parser.add_argument("--review-max-edge", type=int, default=1600)
    parser.add_argument("--realesrgan-binary", type=Path, default=Path("tools/realesrgan-ncnn-vulkan"))
    parser.add_argument("--realesrgan-model-dir", type=Path, default=Path("tools/models"))
    parser.add_argument("--realesrgan-model", default="realesrgan-x4plus")
    parser.add_argument("--tile-size", type=int, default=512)
    parser.add_argument("--thread-counts", default="1:1:1")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-sr-input-pixels", type=int, default=5_000_000)
    parser.add_argument("--max-sr-input-edge", type=int, default=4300)
    parser.add_argument("--sr-dpi-threshold", type=float, default=160.0)
    parser.add_argument("--keep-masters", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--only", action="append")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    progress.plan(["扫描图片", "逐张高清化处理", "生成审计报告", "生成缩略图联系表", "完成"])
    progress.stage(1)
    specs = discover_sources(args.input_dir, args.only)
    if not specs:
        raise SystemExit("No root-level image files with parseable physical dimensions were found.")

    progress.stage(2)
    total = len(specs)
    rows: list[dict[str, str | int | float]] = []
    review_paths: list[Path] = []
    for index, spec in enumerate(specs, start=1):
        progress.step(spec.path.name, index, total, "start")
        print(f"[{index}/{total}] {spec.path.name}", flush=True)
        row = process_one(spec, args)
        rows.append(row)
        review_paths.append(Path(row["review"]))
        backend = "超分高清化" if str(row.get("backend", "")).startswith("realesrgan") else "基础缩放"
        progress.step(f"{spec.path.name}（{backend}）", index, total, "ok")

    progress.stage(3)
    write_report(rows, args.output_dir, args.dpi)
    progress.stage(4)
    build_contact_sheet(review_paths, args.output_dir / "review" / "contact_sheet.jpg")
    progress.stage(5)
    if not args.keep_masters:
        shutil.rmtree(args.output_dir / "_masters", ignore_errors=True)
    progress.done(str(args.output_dir))


if __name__ == "__main__":
    main()
