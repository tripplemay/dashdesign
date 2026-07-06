#!/usr/bin/env python3
"""Prepare agent-generated images for large-format print output.

The script reads physical dimensions from filenames such as
``120乘以80海报1.jpg`` or ``80乘180 4.jpg``. It writes enhanced copies to a
separate output directory and never modifies originals.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import gc
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


Image.MAX_IMAGE_PIXELS = None

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SIZE_RE = re.compile(r"(\d+)\s*乘(?:以)?\s*(\d+)")


@dataclass(frozen=True)
class SourceSpec:
    path: Path
    width_cm: int
    height_cm: int
    source_width: int
    source_height: int


def parse_size_from_name(path: Path) -> tuple[int, int] | None:
    match = SIZE_RE.search(path.name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def target_pixels(width_cm: int, height_cm: int, dpi: int) -> tuple[int, int]:
    return (
        int(round(width_cm / 2.54 * dpi)),
        int(round(height_cm / 2.54 * dpi)),
    )


def effective_dpi(spec: SourceSpec) -> float:
    width_dpi = spec.source_width / (spec.width_cm / 2.54)
    height_dpi = spec.source_height / (spec.height_cm / 2.54)
    return min(width_dpi, height_dpi)


def aspect_delta_percent(source_size: tuple[int, int], target_size: tuple[int, int]) -> float:
    source_ratio = source_size[0] / source_size[1]
    target_ratio = target_size[0] / target_size[1]
    return (source_ratio / target_ratio - 1.0) * 100.0


def fit_with_blurred_background(
    image: Image.Image,
    target_size: tuple[int, int],
) -> tuple[Image.Image, tuple[int, int]]:
    target_width, target_height = target_size
    source_width, source_height = image.size

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

    scale = min(target_width / source_width, target_height / source_height)
    content_size = (
        max(1, int(round(source_width * scale))),
        max(1, int(round(source_height * scale))),
    )
    content = image.resize(content_size, Image.Resampling.LANCZOS)

    x = (target_width - content_size[0]) // 2
    y = (target_height - content_size[1]) // 2
    background.paste(content, (x, y))
    return background, content_size


def enhance(image: Image.Image) -> Image.Image:
    image = ImageEnhance.Contrast(image).enhance(1.025)
    image = ImageEnhance.Color(image).enhance(1.015)
    return image.filter(ImageFilter.UnsharpMask(radius=1.25, percent=115, threshold=3))


def save_print_image(
    image: Image.Image,
    destination: Path,
    dpi: int,
    icc_profile: bytes | None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() in {".jpg", ".jpeg"}:
        save_image = image.convert("RGB")
        kwargs = {
            "quality": 95,
            "subsampling": 0,
            "progressive": True,
            "optimize": True,
            "dpi": (dpi, dpi),
        }
        if icc_profile:
            kwargs["icc_profile"] = icc_profile
        save_image.save(destination, **kwargs)
        return

    image.save(destination, optimize=True, compress_level=6, dpi=(dpi, dpi))


def process_one(
    spec: SourceSpec,
    output_dir: Path,
    dpi: int,
    aspect_tolerance: float,
) -> dict[str, str | int | float]:
    target_size = target_pixels(spec.width_cm, spec.height_cm, dpi)
    output_path = output_dir / spec.path.name

    with Image.open(spec.path) as raw:
        icc_profile = raw.info.get("icc_profile")
        image = ImageOps.exif_transpose(raw).convert("RGB")

    delta = aspect_delta_percent(image.size, target_size)
    if abs(delta) <= aspect_tolerance:
        prepared = image.resize(target_size, Image.Resampling.LANCZOS)
        layout = "resized"
        content_size = target_size
    else:
        prepared, content_size = fit_with_blurred_background(image, target_size)
        layout = "centered_with_blurred_background"

    prepared = enhance(prepared)
    save_print_image(prepared, output_path, dpi, icc_profile)

    row: dict[str, str | int | float] = {
        "source": spec.path.name,
        "output": str(output_path),
        "target_cm": f"{spec.width_cm}x{spec.height_cm}",
        "source_px": f"{spec.source_width}x{spec.source_height}",
        "output_px": f"{target_size[0]}x{target_size[1]}",
        "content_px": f"{content_size[0]}x{content_size[1]}",
        "source_effective_dpi": round(effective_dpi(spec), 1),
        "target_dpi": dpi,
        "scale_x": round(target_size[0] / spec.source_width, 2),
        "scale_y": round(target_size[1] / spec.source_height, 2),
        "aspect_delta_percent": round(delta, 1),
        "layout": layout,
    }

    image.close()
    prepared.close()
    gc.collect()
    return row


def matches_requested_file(path: Path, requested: list[str] | None) -> bool:
    if not requested:
        return True

    return any(
        path.name == pattern
        or str(path) == pattern
        or fnmatch.fnmatch(path.name, pattern)
        for pattern in requested
    )


def discover_sources(input_dir: Path, requested: list[str] | None = None) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if not matches_requested_file(path, requested):
            continue

        size = parse_size_from_name(path)
        if not size:
            continue

        with Image.open(path) as image:
            source_width, source_height = image.size
        specs.append(
            SourceSpec(
                path=path,
                width_cm=size[0],
                height_cm=size[1],
                source_width=source_width,
                source_height=source_height,
            )
        )
    return specs


def write_report(
    rows: list[dict[str, str | int | float]],
    output_dir: Path,
    dpi: int,
) -> None:
    csv_path = output_dir / "print_audit.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    mismatched = [
        row
        for row in rows
        if row["layout"] == "centered_with_blurred_background"
    ]
    low_dpi = [
        row
        for row in rows
        if isinstance(row["source_effective_dpi"], float)
        and row["source_effective_dpi"] < 80
    ]

    readme_path = output_dir / "README_PRINT.md"
    lines = [
        "# Print Output Notes",
        "",
        "Generated files are enhanced copies; original images were not modified.",
        "",
        "Defaults used:",
        f"- {dpi} DPI target for large-format print",
        "- JPEG quality 95 with 4:4:4 chroma",
        "- Mild contrast/color enhancement and unsharp mask",
        "- Blurred background extension when source and target aspect ratios differ",
        "",
        f"Processed images: {len(rows)}",
        f"Aspect-ratio adjusted images: {len(mismatched)}",
        f"Sources below 80 effective DPI: {len(low_dpi)}",
        "",
        "Important: upscaling improves output pixel density but cannot recover text or QR-code detail that is not present in the original image.",
        "For close-viewing print, request regenerated source art at the correct aspect ratio and higher native resolution when possible.",
        "",
        "See `print_audit.csv` for per-file dimensions and scaling factors.",
        "",
    ]
    readme_path.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("."),
        help="Directory containing source images.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("print_ready_150dpi"),
        help="Directory for enhanced copies and reports.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Target print DPI. Use 200 or 300 only if your print shop requires it.",
    )
    parser.add_argument(
        "--aspect-tolerance",
        type=float,
        default=1.5,
        help="Allowed aspect-ratio delta before using background extension.",
    )
    parser.add_argument(
        "--only",
        action="append",
        help="Only process this filename or shell-style filename pattern. Can be repeated.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    specs = discover_sources(input_dir, args.only)
    if not specs:
        raise SystemExit(f"No image files with parseable dimensions found in {input_dir}")

    rows = []
    for index, spec in enumerate(specs, start=1):
        print(f"[{index}/{len(specs)}] {spec.path.name}")
        rows.append(process_one(spec, output_dir, args.dpi, args.aspect_tolerance))

    write_report(rows, output_dir, args.dpi)
    print(f"Done. Output written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
