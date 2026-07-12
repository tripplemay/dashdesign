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
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import progress

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


Image.MAX_IMAGE_PIXELS = None

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
# Accept 乘/乘以/x/×/* as the separator (e.g. 120乘以80, 200x80, 80×180); the
# digits may be full-width (int() parses those too). Deliberately excludes -/_
# so dated names like 2025-01-01 are not misread as dimensions. Keep in sync
# with ui/pages/batch_page.py:_SIZE_RE (an independent copy).
SIZE_RE = re.compile(r"(\d+)\s*(?:乘以|乘|[xX*×])\s*(\d+)")


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


def size_from_print_spec(directory: Path) -> tuple[int, int] | None:
    """Physical size from a ``print_spec.json`` sidecar in ``directory``.

    The text-to-image / full-poster packages name their master ``master.png``
    (no size in the filename) and record the physical size in this sidecar —
    same convention gpt_image_rebuild already resolves. Returns None when the
    sidecar is missing or unusable.
    """
    spec_path = directory / "print_spec.json"
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        width_cm = float(spec["width_cm"])
        height_cm = float(spec["height_cm"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    if width_cm <= 0 or height_cm <= 0:
        return None
    return int(round(width_cm)), int(round(height_cm))


def target_pixels(width_cm: int, height_cm: int, dpi: int) -> tuple[int, int]:
    return (
        int(round(width_cm / 2.54 * dpi)),
        int(round(height_cm / 2.54 * dpi)),
    )


def effective_dpi(spec: SourceSpec) -> float:
    width_dpi = spec.source_width / (spec.width_cm / 2.54)
    height_dpi = spec.source_height / (spec.height_cm / 2.54)
    return min(width_dpi, height_dpi)


# 超分输入上限：源边过大时超分很慢且收益低，直接走 PIL。
SR_MAX_INPUT_EDGE = 4300
SR_MAX_INPUT_PIXELS = 12_000_000

# 超分崩溃（Windows 退出码 3221225477 = 0xC0000005 访问越界）多为显存吃紧或
# 驱动不稳定，用更小的分块重试往往能成功；逐级缩小，仍失败才让调用方回退。
SR_TILE_SIZES = (512, 256, 128)


def run_realesrgan(
    source: Path,
    destination: Path,
    binary: Path,
    model_dir: Path,
    model: str = "realesrgan-x4plus",
    tile_size: int = 512,
    thread_counts: str = "1:1:1",
    timeout: int = 900,
) -> None:
    """Real-ESRGAN x4 super-resolution (ncnn/vulkan binary). Raises on failure."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(binary), "-i", str(source), "-o", str(destination),
        "-m", str(model_dir), "-s", "4", "-n", model,
        "-t", str(tile_size), "-j", thread_counts, "-f", "png",
    ]
    subprocess.run(
        command, check=True, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def run_realesrgan_with_retry(
    source: Path,
    destination: Path,
    binary: Path,
    model_dir: Path,
    model: str = "realesrgan-x4plus",
    thread_counts: str = "1:1:1",
    timeout: int = 900,
    tile_sizes: tuple[int, ...] = SR_TILE_SIZES,
) -> None:
    """Real-ESRGAN x4 with progressive tile shrinking on crash.

    A native crash (nonzero exit, e.g. Windows access violation 3221225477)
    usually means the GPU ran out of memory or the driver misbehaved on a large
    tile — a smaller tile often succeeds. Retries down ``tile_sizes`` and, only
    after every tile fails, re-raises the last ``CalledProcessError`` so the
    caller can still fall back to plain scaling. ``TimeoutExpired`` is not
    retried: a smaller tile cannot fix a wall-clock timeout.
    """
    last_error: subprocess.CalledProcessError | None = None
    for tile_size in tile_sizes:
        try:
            run_realesrgan(
                source,
                destination,
                binary,
                model_dir,
                model=model,
                tile_size=tile_size,
                thread_counts=thread_counts,
                timeout=timeout,
            )
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            print(
                f"[warn] Real-ESRGAN tile={tile_size} 崩溃（退出码 {exc.returncode}），"
                "改用更小分块重试。",
                file=sys.stderr,
            )
    if last_error is not None:
        raise last_error
    raise ValueError("tile_sizes 不能为空")


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


def fit_within(
    image: Image.Image,
    target_size: tuple[int, int],
) -> tuple[Image.Image, tuple[int, int]]:
    """Scale ``image`` proportionally to fit inside ``target_size`` — no padding,
    no crop, no distortion.

    Returns the resized image and its ``(width, height)``. The result keeps the
    source aspect ratio: it fills the target on one axis and is smaller on the
    other, so a declared print size whose aspect differs from the source no
    longer gets a blurred/letterbox border — it is simply enlarged proportionally
    (成品以声明尺寸为上限，某一边可能略小于声明值).
    """
    target_width, target_height = target_size
    scale = min(target_width / image.width, target_height / image.height)
    fitted = (
        max(1, int(round(image.width * scale))),
        max(1, int(round(image.height * scale))),
    )
    return image.resize(fitted, Image.Resampling.LANCZOS), fitted


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
        # 比例不匹配时等比缩放到适应目标框，按适应后尺寸输出（不补边、不裁切、不变形）。
        prepared, content_size = fit_within(image, target_size)
        layout = "proportional"

    prepared = enhance(prepared)
    save_print_image(prepared, output_path, dpi, icc_profile)

    row: dict[str, str | int | float] = {
        "source": spec.path.name,
        "output": str(output_path),
        "target_cm": f"{spec.width_cm}x{spec.height_cm}",
        "source_px": f"{spec.source_width}x{spec.source_height}",
        # 成品实际像素 = 输出像素（等比适应后无填充，二者一致）。
        "output_px": f"{content_size[0]}x{content_size[1]}",
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
    # 目录级回退：文生图/整幅海报产包的 master.png 文件名不带尺寸，
    # 尺寸在同目录 print_spec.json 里（对该目录的所有候选图都适用）。
    spec_size = size_from_print_spec(input_dir)
    for path in sorted(input_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if not matches_requested_file(path, requested):
            continue

        size = parse_size_from_name(path) or spec_size
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
        if row["layout"] == "proportional"
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

    progress.plan(["扫描图片", "逐张处理", "生成审计报告", "完成"])
    progress.stage(1)
    specs = discover_sources(input_dir, args.only)
    if not specs:
        raise SystemExit(f"No image files with parseable dimensions found in {input_dir}")

    progress.stage(2)
    total = len(specs)
    rows = []
    for index, spec in enumerate(specs, start=1):
        progress.step(spec.path.name, index, total, "start")
        print(f"[{index}/{total}] {spec.path.name}", flush=True)
        rows.append(process_one(spec, output_dir, args.dpi, args.aspect_tolerance))
        progress.step(spec.path.name, index, total, "ok")

    progress.stage(3)
    write_report(rows, output_dir, args.dpi)
    progress.stage(4)
    print(f"Done. Output written to {output_dir}")
    progress.done(str(output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
