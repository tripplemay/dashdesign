"""Unit tests for the GPT edit page's source-driven print-size prefill.

The prefill keeps the UI size box consistent with what the source actually
implies, so an explicit --width-cm/--height-cm (which the worker treats as an
override) never silently reprints a well-named source at the box's default size.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from ui.print_size import suggest_print_size_cm


def _write_png(path: Path, size: tuple[int, int]) -> None:
    Image.new("RGB", size, (120, 130, 140)).save(path)


def _write_print_spec(directory: Path, width_cm: float, height_cm: float) -> None:
    (directory / "print_spec.json").write_text(
        json.dumps({"width_cm": width_cm, "height_cm": height_cm, "dpi": 200}),
        encoding="utf-8",
    )


class TestSuggestPrintSize:
    def test_filename_token_wins(self, tmp_path: Path) -> None:
        source = tmp_path / "海报_80乘120.png"
        _write_png(source, (1024, 1536))
        assert suggest_print_size_cm(source) == (80, 120)

    def test_filename_token_beats_print_spec(self, tmp_path: Path) -> None:
        source = tmp_path / "海报_80乘120.png"
        _write_png(source, (1024, 1536))
        _write_print_spec(tmp_path, 40, 60)
        assert suggest_print_size_cm(source) == (80, 120)

    def test_falls_back_to_sibling_print_spec(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        _write_png(source, (1200, 800))
        _write_print_spec(tmp_path, 90, 60)
        assert suggest_print_size_cm(source) == (90, 60)

    def test_falls_back_to_ancestor_directory(self, tmp_path: Path) -> None:
        workdir = tmp_path / "20260708_184744_60x90_background_t2i"
        workdir.mkdir()
        source = workdir / "master.png"
        _write_png(source, (800, 1200))
        assert suggest_print_size_cm(source) == (60, 90)

    def test_pixel_aspect_fallback_for_plain_portrait_photo(self, tmp_path: Path) -> None:
        # The log-2 case: a bare Desktop photo with no size hint anywhere.
        source = tmp_path / "13131.jpg"
        _write_png(source, (1024, 1536))  # 2:3 portrait
        width_cm, height_cm = suggest_print_size_cm(source)
        # Long edge scaled to 120cm, aspect preserved (no stretch).
        assert height_cm == 120.0
        assert width_cm == 80.0

    def test_pixel_aspect_fallback_for_landscape_photo(self, tmp_path: Path) -> None:
        source = tmp_path / "photo.jpg"
        _write_png(source, (1500, 1000))  # 3:2 landscape
        width_cm, height_cm = suggest_print_size_cm(source)
        assert width_cm == 120.0
        assert height_cm == 80.0

    def test_returns_none_for_missing_source(self, tmp_path: Path) -> None:
        assert suggest_print_size_cm(tmp_path / "nope.png") is None

    def test_date_like_directory_is_not_mistaken_for_size(self, tmp_path: Path) -> None:
        # 20260708 has no 乘/x separator, so it must not parse as a size token.
        workdir = tmp_path / "20260708_184744_background_t2i"
        workdir.mkdir()
        source = workdir / "photo.jpg"
        _write_png(source, (1000, 1000))
        assert suggest_print_size_cm(source) == (120.0, 120.0)  # falls through to pixels
