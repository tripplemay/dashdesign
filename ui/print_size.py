"""Suggest a print size (cm) to prefill when a source image is chosen.

The GPT edit page always sends explicit ``--width-cm``/``--height-cm`` to the
worker, which treats them as an override of its own inference. So the size shown
in the UI must match what the source actually implies — otherwise a well-named
source like ``海报_80乘120.png`` would be silently reprinted at the box's default
size, and a portrait photo left at a landscape default would be stretched.

Resolution order mirrors the worker's ``resolve_physical_size``
(scripts/gpt_image_rebuild.py): filename size token → sibling ``print_spec.json``
→ ancestor directory name. When none is present we fall back to the source's own
pixel aspect (long edge scaled to a sensible default) so at least the aspect is
correct and the print is not stretched.

``_SIZE_RE`` is an independent copy kept identical to
``prepare_print_assets.SIZE_RE`` / ``ui/pages/batch_page._SIZE_RE`` on purpose
(scripts are not importable from the GUI process).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_SIZE_RE = re.compile(r"(\d+)\s*(?:乘以|乘|[xX*×])\s*(\d+)")
# 无任何尺寸线索时，按源图比例取长边为该 cm 值，保证比例正确、成品不被拉伸。
_DEFAULT_LONG_EDGE_CM = 120.0


def _positive_pair(width_cm: float, height_cm: float) -> "tuple[float, float] | None":
    if width_cm <= 0 or height_cm <= 0:
        return None
    return float(width_cm), float(height_cm)


def _from_filename(source: Path) -> "tuple[float, float] | None":
    match = _SIZE_RE.search(source.name)
    if not match:
        return None
    return _positive_pair(float(match.group(1)), float(match.group(2)))


def _from_print_spec(source: Path) -> "tuple[float, float] | None":
    spec_path = source.parent / "print_spec.json"
    if not spec_path.is_file():
        return None
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        return _positive_pair(float(spec["width_cm"]), float(spec["height_cm"]))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _from_ancestors(source: Path) -> "tuple[float, float] | None":
    for part in reversed(source.parent.parts):
        match = _SIZE_RE.search(part)
        if match:
            return _positive_pair(float(match.group(1)), float(match.group(2)))
    return None


def _from_pixels(source: Path) -> "tuple[float, float] | None":
    try:
        from PIL import Image

        with Image.open(source) as image:
            pixel_width, pixel_height = image.size
    except (OSError, ValueError):
        return None
    if pixel_width <= 0 or pixel_height <= 0:
        return None
    scale = _DEFAULT_LONG_EDGE_CM / max(pixel_width, pixel_height)
    return round(pixel_width * scale, 1), round(pixel_height * scale, 1)


def suggest_print_size_cm(source: Path) -> "tuple[float, float] | None":
    """Best-effort print size (cm) for prefill, or ``None`` if unreadable."""
    if not source.is_file():
        return None
    for candidate in (
        _from_filename(source),
        _from_print_spec(source),
        _from_ancestors(source),
        _from_pixels(source),
    ):
        if candidate:
            return candidate
    return None
