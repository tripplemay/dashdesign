#!/usr/bin/env python3
"""Remove a QR-code area from one image while preserving the rest of the image."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

import progress


Image.MAX_IMAGE_PIXELS = None


def parse_box(value: str) -> tuple[int, int, int, int]:
    parts = [part.strip() for part in value.replace("，", ",").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("Box must be x1,y1,x2,y2")
    try:
        left, top, right, bottom = [int(float(part)) for part in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Box values must be numbers") from exc
    if right <= left or bottom <= top:
        raise argparse.ArgumentTypeError("Box must satisfy x2>x1 and y2>y1")
    return left, top, right, bottom


def scale_box(
    box: tuple[int, int, int, int],
    reference_size: tuple[int, int] | None,
    target_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    if reference_size is None:
        return box
    sx = target_size[0] / reference_size[0]
    sy = target_size[1] / reference_size[1]
    return (
        round(box[0] * sx),
        round(box[1] * sy),
        round(box[2] * sx),
        round(box[3] * sy),
    )


def remove_area(
    image: Image.Image,
    box: tuple[int, int, int, int],
    margin_ratio: float,
    radius: int,
) -> Image.Image:
    left, top, right, bottom = box
    left = max(0, left)
    top = max(0, top)
    right = min(image.width, right)
    bottom = min(image.height, bottom)
    if right <= left or bottom <= top:
        raise ValueError("The removal box is outside the image")

    margin_x = round((right - left) * margin_ratio)
    margin_y = round((bottom - top) * margin_ratio)
    crop_box = (
        max(0, left - margin_x),
        max(0, top - margin_y),
        min(image.width, right + margin_x),
        min(image.height, bottom + margin_y),
    )

    crop = image.crop(crop_box).convert("RGB")
    arr = cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2BGR)
    mask = np.zeros(arr.shape[:2], dtype=np.uint8)
    mask_left = left - crop_box[0]
    mask_top = top - crop_box[1]
    mask_right = right - crop_box[0]
    mask_bottom = bottom - crop_box[1]
    cv2.rectangle(mask, (mask_left, mask_top), (mask_right, mask_bottom), 255, thickness=-1)

    kernel_size = max(3, round(min(right - left, bottom - top) * 0.06))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=2)

    cleaned = cv2.inpaint(arr, mask, inpaintRadius=max(5, radius), flags=cv2.INPAINT_TELEA)
    cleaned_rgb = cv2.cvtColor(cleaned, cv2.COLOR_BGR2RGB)

    output = image.copy()
    output.paste(Image.fromarray(cleaned_rgb), crop_box[:2])
    crop.close()
    return output


def save_image(image: Image.Image, source: Path, destination: Path, dpi: tuple[float, float] | None, icc: bytes | None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix.lower() in {".jpg", ".jpeg"}:
        kwargs = {
            "quality": 95,
            "subsampling": 0,
            "progressive": True,
            "optimize": True,
        }
        if dpi:
            kwargs["dpi"] = dpi
        if icc:
            kwargs["icc_profile"] = icc
        image.convert("RGB").save(destination, **kwargs)
        return
    image.save(destination, optimize=True, compress_level=6, dpi=dpi)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("single_no_qr_200dpi"))
    parser.add_argument("--box", type=parse_box, required=True, help="Removal box as x1,y1,x2,y2")
    parser.add_argument(
        "--reference-size",
        help="Optional coordinate reference size as WIDTHxHEIGHT. If set, box is scaled to input image size.",
    )
    parser.add_argument("--margin-ratio", type=float, default=0.55)
    parser.add_argument("--inpaint-radius", type=int, default=21)
    parser.add_argument("--review-max-edge", type=int, default=1800)
    return parser


def parse_reference_size(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    normalized = value.lower().replace("×", "x")
    parts = normalized.split("x")
    if len(parts) != 2:
        raise ValueError("--reference-size must be WIDTHxHEIGHT")
    return int(parts[0]), int(parts[1])


def main() -> None:
    args = build_parser().parse_args()
    progress.plan(["换算清除区域", "打开图片", "去除二维码区域", "保存成品与预览", "完成"])
    progress.stage(1)
    reference_size = parse_reference_size(args.reference_size)

    progress.stage(2)
    with Image.open(args.input) as raw:
        dpi = raw.info.get("dpi")
        icc = raw.info.get("icc_profile")
        image = ImageOps.exif_transpose(raw).convert("RGB")

    progress.stage(3)
    box = scale_box(args.box, reference_size, image.size)
    output = remove_area(image, box, args.margin_ratio, args.inpaint_radius)

    progress.stage(4)
    destination = args.output or args.output_dir / f"{args.input.stem}_no_qr{args.input.suffix}"
    save_image(output, args.input, destination, dpi, icc)

    review = output.copy()
    review.thumbnail((args.review_max_edge, args.review_max_edge), Image.Resampling.LANCZOS)
    review_path = destination.parent / "review" / destination.with_suffix(".jpg").name
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review.save(review_path, quality=92, optimize=True)

    print(destination)
    print(review_path)
    print(f"image_size={image.width}x{image.height}")
    print(f"removal_box={box[0]},{box[1]},{box[2]},{box[3]}")

    progress.stage(5)
    progress.done(str(destination))

    image.close()
    output.close()
    review.close()


if __name__ == "__main__":
    main()
