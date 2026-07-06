#!/usr/bin/env python3
"""Evaluate print enhancement methods on representative poster crops."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from prepare_print_assets import (
    aspect_delta_percent,
    enhance,
    parse_size_from_name,
    target_pixels,
)


Image.MAX_IMAGE_PIXELS = None


@dataclass(frozen=True)
class CropSpec:
    source: str
    crop_id: str
    label: str
    box: tuple[int, int, int, int]
    risk: str


CROPS = [
    CropSpec(
        source="160乘以160.jpg",
        crop_id="square_title",
        label="160 square title",
        box=(150, 55, 1145, 250),
        risk="large AI-rendered Chinese title, visible soft edges",
    ),
    CropSpec(
        source="160乘以160.jpg",
        crop_id="square_small_text",
        label="160 square small text",
        box=(850, 455, 1215, 560),
        risk="small white Chinese copy on colorful background",
    ),
    CropSpec(
        source="160乘以160.jpg",
        crop_id="square_qr",
        label="160 square QR",
        box=(1000, 1000, 1235, 1235),
        risk="QR code must be deterministic and scannable",
    ),
    CropSpec(
        source="200乘以80.jpg",
        crop_id="wide_title",
        label="200x80 wide title",
        box=(730, 55, 2500, 230),
        risk="wide-format headline with glow and drop shadow",
    ),
    CropSpec(
        source="200乘以80.jpg",
        crop_id="wide_left_small_text",
        label="200x80 left small text",
        box=(220, 265, 980, 490),
        risk="dense white text, low effective source DPI",
    ),
    CropSpec(
        source="200乘以80.jpg",
        crop_id="wide_right_small_text",
        label="200x80 right small text",
        box=(1880, 260, 3080, 565),
        risk="dense text over orange background",
    ),
    CropSpec(
        source="80乘以180 （打孔）.jpg",
        crop_id="vertical_title",
        label="80x180 vertical title",
        box=(25, 60, 410, 185),
        risk="extremely low-resolution vertical title",
    ),
    CropSpec(
        source="80乘以180 （打孔）.jpg",
        crop_id="vertical_small_text",
        label="80x180 vertical small text",
        box=(5, 500, 225, 600),
        risk="tiny explanatory copy in a 426px-wide source",
    ),
    CropSpec(
        source="80乘以180 （打孔）.jpg",
        crop_id="vertical_qr",
        label="80x180 vertical QR",
        box=(150, 850, 286, 990),
        risk="QR code from a very low-resolution source",
    ),
]


def output_scale(source_size: tuple[int, int], target_size: tuple[int, int], tolerance: float) -> tuple[float, float]:
    delta = aspect_delta_percent(source_size, target_size)
    if abs(delta) <= tolerance:
        return target_size[0] / source_size[0], target_size[1] / source_size[1]
    scale = min(target_size[0] / source_size[0], target_size[1] / source_size[1])
    return scale, scale


def target_crop_size(crop: Image.Image, sx: float, sy: float) -> tuple[int, int]:
    return max(1, round(crop.width * sx)), max(1, round(crop.height * sy))


def pil_current(crop: Image.Image, size: tuple[int, int]) -> Image.Image:
    return enhance(crop.resize(size, Image.Resampling.LANCZOS))


def pil_text_strong(crop: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = crop.resize(size, Image.Resampling.LANCZOS)
    image = ImageEnhance.Contrast(image).enhance(1.04)
    image = image.filter(ImageFilter.UnsharpMask(radius=0.7, percent=210, threshold=2))
    return image.filter(ImageFilter.UnsharpMask(radius=0.35, percent=80, threshold=1))


def load_cv2():
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def opencv_edsr(crop: Image.Image, size: tuple[int, int], model_path: Path) -> Image.Image:
    cv2 = load_cv2()
    if cv2 is None:
        raise RuntimeError("cv2 is not available")
    if not model_path.exists():
        raise RuntimeError(f"EDSR model not found: {model_path}")

    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(str(model_path))
    sr.setModel("edsr", 4)
    rgb = np.array(crop.convert("RGB"))
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    upsampled = sr.upsample(bgr)
    upsampled = cv2.cvtColor(upsampled, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(upsampled)
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image.filter(ImageFilter.UnsharpMask(radius=0.55, percent=75, threshold=2))


def run_realesrgan(
    crop: Image.Image,
    size: tuple[int, int],
    binary: Path,
    model_dir: Path,
    model_name: str,
    scratch_dir: Path,
) -> Image.Image:
    if not binary.exists():
        raise RuntimeError(f"Real-ESRGAN binary not found: {binary}")
    if not model_dir.exists():
        raise RuntimeError(f"Real-ESRGAN model dir not found: {model_dir}")

    scratch_dir.mkdir(parents=True, exist_ok=True)
    input_path = scratch_dir / f"{model_name}_input.png"
    output_path = scratch_dir / f"{model_name}_x4.png"
    crop.save(input_path)

    subprocess.run(
        [
            str(binary),
            "-i",
            str(input_path),
            "-o",
            str(output_path),
            "-m",
            str(model_dir),
            "-s",
            "4",
            "-n",
            model_name,
            "-f",
            "png",
        ],
        check=True,
        timeout=180,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    with Image.open(output_path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image.filter(ImageFilter.UnsharpMask(radius=0.45, percent=65, threshold=2))


def grayscale_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float32)


def quality_metrics(image: Image.Image) -> dict[str, float]:
    gray = grayscale_array(image)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:] = np.abs(gray[:, 1:] - gray[:, :-1])
    gy[1:, :] = np.abs(gray[1:, :] - gray[:-1, :])
    grad = np.sqrt(gx * gx + gy * gy)

    if gray.shape[0] > 2 and gray.shape[1] > 2:
        lap = (
            4 * gray[1:-1, 1:-1]
            - gray[1:-1, :-2]
            - gray[1:-1, 2:]
            - gray[:-2, 1:-1]
            - gray[2:, 1:-1]
        )
        lap_abs = np.abs(lap)
    else:
        lap_abs = np.abs(gray - gray.mean())

    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    clipped = np.mean((rgb <= 2) | (rgb >= 253))

    return {
        "grad_mean": round(float(grad.mean()), 3),
        "grad_p95": round(float(np.percentile(grad, 95)), 3),
        "lap_abs_mean": round(float(lap_abs.mean()), 3),
        "lap_abs_p95": round(float(np.percentile(lap_abs, 95)), 3),
        "edge_density": round(float(np.mean(grad > 18)), 4),
        "clip_ratio": round(float(clipped), 4),
    }


def detect_qr(image: Image.Image) -> tuple[bool, int]:
    cv2 = load_cv2()
    if cv2 is None:
        return False, 0
    probe = image.convert("RGB")
    max_edge = max(probe.size)
    if max_edge > 2200:
        scale = 2200 / max_edge
        probe = probe.resize((round(probe.width * scale), round(probe.height * scale)), Image.Resampling.LANCZOS)
    arr = np.array(probe)
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    detector = cv2.QRCodeDetector()
    data, points, _ = detector.detectAndDecode(bgr)
    return bool(data), len(data or "")


def center_zoom(image: Image.Image, tile_size: tuple[int, int]) -> Image.Image:
    tile_w, tile_h = tile_size
    crop_w = min(tile_w, image.width)
    crop_h = min(tile_h, image.height)
    left = max(0, (image.width - crop_w) // 2)
    top = max(0, (image.height - crop_h) // 2)
    zoom = image.crop((left, top, left + crop_w, top + crop_h))
    tile = Image.new("RGB", tile_size, "white")
    tile.paste(zoom.convert("RGB"), ((tile_w - zoom.width) // 2, (tile_h - zoom.height) // 2))
    return tile


def draw_label(tile: Image.Image, text: str) -> Image.Image:
    label_h = 92
    output = Image.new("RGB", (tile.width, tile.height + label_h), "white")
    output.paste(tile, (0, label_h))
    draw = ImageDraw.Draw(output)
    draw.multiline_text((10, 8), text, fill=(0, 0, 0), spacing=4)
    return output


def build_zoom_sheet(
    crop_id: str,
    rendered: list[tuple[str, Path, dict[str, str | float | int]]],
    destination: Path,
) -> None:
    tiles: list[Image.Image] = []
    for method, path, row in rendered:
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
        zoom = center_zoom(image, (900, 520))
        label = (
            f"{method}\n"
            f"{image.width}x{image.height}px | grad {row.get('grad_mean')} | lap {row.get('lap_abs_mean')}\n"
            f"QR {row.get('qr_decoded')}"
        )
        tiles.append(draw_label(zoom, label))

    if not tiles:
        return

    sheet = Image.new("RGB", (tiles[0].width * len(tiles), tiles[0].height), "white")
    for index, tile in enumerate(tiles):
        sheet.paste(tile, (index * tile.width, 0))
    destination.mkdir(parents=True, exist_ok=True)
    sheet.save(destination / f"{crop_id}_zoom.jpg", quality=92)


def write_report(
    output_dir: Path,
    rows: list[dict[str, str | float | int]],
    failures: list[dict[str, str]],
    dpi: int,
) -> None:
    csv_path = output_dir / "metrics.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    method_rows: dict[str, list[dict[str, str | float | int]]] = {}
    for row in rows:
        method_rows.setdefault(str(row["method"]), []).append(row)

    lines = [
        "# Print Enhancement Evaluation",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Target DPI for crop scale: {dpi}",
        "",
        "## Methods",
        "",
        "- `pil_current`: current workflow crop equivalent, Lanczos resize + mild color/contrast + UnsharpMask.",
        "- `pil_text_strong`: stronger deterministic sharpening tuned for text edges.",
        "- `opencv_edsr_x4`: OpenCV dnn_superres EDSR x4, then resize to target crop size.",
        "- `realesrgan_x4plus`: Real-ESRGAN NCNN general x4 model, then resize to target crop size.",
        "- `realesrgan_x4plus_anime`: Real-ESRGAN NCNN anime/illustration x4 model, then resize to target crop size.",
        "",
        "## Aggregate Metrics",
        "",
        "Metrics are no-reference proxies. Higher gradient/laplacian values usually mean sharper edges, but can also mean halos, ringing, or noisy artifacts.",
        "",
        "| method | crops | grad_mean_avg | lap_abs_mean_avg | edge_density_avg | clip_ratio_avg | qr_decoded |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for method, items in sorted(method_rows.items()):
        count = len(items)
        grad = sum(float(row["grad_mean"]) for row in items) / count
        lap = sum(float(row["lap_abs_mean"]) for row in items) / count
        edge = sum(float(row["edge_density"]) for row in items) / count
        clip = sum(float(row["clip_ratio"]) for row in items) / count
        qr_hits = sum(1 for row in items if row["qr_decoded"] == "yes")
        lines.append(
            f"| `{method}` | {count} | {grad:.3f} | {lap:.3f} | {edge:.4f} | {clip:.4f} | {qr_hits} |"
        )

    lines.extend(
        [
            "",
            "## Files",
            "",
            "- Full crop outputs: `crops/<crop_id>/<method>.png`",
            "- Native-pixel center zoom sheets: `zoom_sheets/*_zoom.jpg`",
            "- Raw metrics: `metrics.csv`",
            "",
            "## Failures",
            "",
        ]
    )
    if failures:
        for failure in failures:
            lines.append(f"- `{failure['method']}` on `{failure['crop_id']}`: {failure['error']}")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append(
        "Interpretation rule: QR codes, legal copy, phone numbers, prices, and brand marks should be rebuilt as controlled layers even if an upscaler appears visually sharper."
    )
    lines.append("")

    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    output_dir = args.output_dir
    crops_dir = output_dir / "crops"
    zoom_dir = output_dir / "zoom_sheets"
    scratch_dir = output_dir / "_scratch"
    output_dir.mkdir(parents=True, exist_ok=True)

    method_funcs: list[tuple[str, Callable[[Image.Image, tuple[int, int]], Image.Image]]] = [
        ("pil_current", pil_current),
        ("pil_text_strong", pil_text_strong),
    ]

    edsr_model = args.edsr_model
    if not args.skip_edsr:
        method_funcs.append(("opencv_edsr_x4", lambda crop, size: opencv_edsr(crop, size, edsr_model)))

    if not args.skip_realesrgan:
        binary = args.realesrgan_binary
        model_dir = args.realesrgan_model_dir
        method_funcs.append(
            (
                "realesrgan_x4plus",
                lambda crop, size: run_realesrgan(
                    crop,
                    size,
                    binary,
                    model_dir,
                    "realesrgan-x4plus",
                    scratch_dir / "realesrgan_x4plus",
                ),
            )
        )
        method_funcs.append(
            (
                "realesrgan_x4plus_anime",
                lambda crop, size: run_realesrgan(
                    crop,
                    size,
                    binary,
                    model_dir,
                    "realesrgan-x4plus-anime",
                    scratch_dir / "realesrgan_x4plus_anime",
                ),
            )
        )

    rows: list[dict[str, str | float | int]] = []
    failures: list[dict[str, str]] = []

    for crop_spec in CROPS:
        source_path = args.input_dir / crop_spec.source
        physical_size = parse_size_from_name(source_path)
        if physical_size is None:
            raise ValueError(f"Could not parse physical size from {source_path.name}")

        with Image.open(source_path) as raw:
            source_image = ImageOps.exif_transpose(raw).convert("RGB")

        crop = source_image.crop(crop_spec.box)
        target_size = target_pixels(physical_size[0], physical_size[1], args.dpi)
        sx, sy = output_scale(source_image.size, target_size, args.aspect_tolerance)
        crop_size = target_crop_size(crop, sx, sy)

        crop_out_dir = crops_dir / crop_spec.crop_id
        crop_out_dir.mkdir(parents=True, exist_ok=True)
        crop.save(crop_out_dir / "source_crop.png")

        rendered_for_sheet: list[tuple[str, Path, dict[str, str | float | int]]] = []

        for method, func in method_funcs:
            output_path = crop_out_dir / f"{method}.png"
            try:
                enhanced = func(crop, crop_size)
                enhanced.save(output_path, dpi=(args.dpi, args.dpi))
                metrics = quality_metrics(enhanced)
                qr_decoded, qr_payload_len = detect_qr(enhanced) if "qr" in crop_spec.crop_id else (False, 0)
                row: dict[str, str | float | int] = {
                    "source": crop_spec.source,
                    "crop_id": crop_spec.crop_id,
                    "label": crop_spec.label,
                    "risk": crop_spec.risk,
                    "method": method,
                    "source_crop_px": f"{crop.width}x{crop.height}",
                    "target_crop_px": f"{crop_size[0]}x{crop_size[1]}",
                    "scale_x": round(sx, 3),
                    "scale_y": round(sy, 3),
                    "qr_decoded": "yes" if qr_decoded else "no",
                    "qr_payload_len": qr_payload_len,
                    "output": str(output_path),
                }
                row.update(metrics)
                rows.append(row)
                rendered_for_sheet.append((method, output_path, row))
                enhanced.close()
            except Exception as exc:
                failures.append(
                    {
                        "crop_id": crop_spec.crop_id,
                        "method": method,
                        "error": str(exc).replace("\n", " ")[:500],
                    }
                )

        build_zoom_sheet(crop_spec.crop_id, rendered_for_sheet, zoom_dir)
        crop.close()
        source_image.close()

    if scratch_dir.exists() and args.clean_scratch:
        shutil.rmtree(scratch_dir)

    if not rows:
        raise RuntimeError("No evaluation outputs were generated")

    write_report(output_dir, rows, failures, args.dpi)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, default=Path("quality_eval/run_200dpi"))
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--aspect-tolerance", type=float, default=0.5)
    parser.add_argument("--edsr-model", type=Path, default=Path("tools/sr_models/EDSR_x4.pb"))
    parser.add_argument("--skip-edsr", action="store_true")
    parser.add_argument("--skip-realesrgan", action="store_true")
    parser.add_argument("--realesrgan-binary", type=Path, default=Path("tools/realesrgan-ncnn-vulkan"))
    parser.add_argument("--realesrgan-model-dir", type=Path, default=Path("tools/models"))
    parser.add_argument("--clean-scratch", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
