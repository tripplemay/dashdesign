"""Tests for physical-size parsing from filenames in the batch print pipeline.

The GUI pre-check (ui/pages/batch_page._SIZE_RE) and the worker
(scripts/prepare_print_assets) keep independent copies of the size regex; this
verifies they accept the same broadened set of separators and reject dates.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# scripts/ is not a package and not on the default test path; the worker adds it
# at runtime (runpy from the scripts dir). Mirror that here to import the module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from prepare_print_assets import (  # noqa: E402
    discover_sources,
    parse_size_from_name,
    size_from_print_spec,
)

# Mirror of ui/pages/batch_page._SIZE_RE (that module imports PySide6, which is
# not installed in the headless test env, so re-declare the pattern here and
# assert it matches the worker's).
_GUI_SIZE_RE = re.compile(r"(\d+)\s*(?:乘以|乘|[xX*×])\s*(\d+)")


class TestParseSizeFromName:
    def test_chinese_cheng(self) -> None:
        assert parse_size_from_name(Path("120乘80海报.jpg")) == (120, 80)

    def test_chinese_chengyi(self) -> None:
        assert parse_size_from_name(Path("120乘以80海报.jpg")) == (120, 80)

    def test_ascii_x(self) -> None:
        assert parse_size_from_name(Path("200x80.jpg")) == (200, 80)

    def test_ascii_upper_x(self) -> None:
        assert parse_size_from_name(Path("200X80.jpg")) == (200, 80)

    def test_multiplication_sign(self) -> None:
        assert parse_size_from_name(Path("80×180 4.jpg")) == (80, 180)

    def test_asterisk(self) -> None:
        assert parse_size_from_name(Path("200*80.png")) == (200, 80)

    def test_spaces_around_separator(self) -> None:
        assert parse_size_from_name(Path("200 x 80.jpg")) == (200, 80)

    def test_no_size_returns_none(self) -> None:
        assert parse_size_from_name(Path("poster.jpg")) is None

    def test_date_is_not_a_size(self) -> None:
        # 刻意排除 - / _，避免把日期误当尺寸。
        assert parse_size_from_name(Path("2025-01-01.jpg")) is None
        assert parse_size_from_name(Path("img_2025_01.jpg")) is None


class TestGuiAndWorkerRegexAgree:
    CASES = ["120乘80", "120乘以80", "200x80", "200X80", "80×180", "200*80", "200 x 80"]

    def test_same_matches(self) -> None:
        for stem in self.CASES:
            name = f"{stem}.jpg"
            gui = _GUI_SIZE_RE.search(name)
            worker = parse_size_from_name(Path(name))
            assert (gui is not None) == (worker is not None)
            if gui is not None:
                assert (int(gui.group(1)), int(gui.group(2))) == worker


def _write_spec(directory: Path, width: object = 120.0, height: object = 80.0) -> None:
    import json

    (directory / "print_spec.json").write_text(
        json.dumps({"width_cm": width, "height_cm": height}), encoding="utf-8"
    )


class TestSizeFromPrintSpec:
    def test_reads_and_rounds(self, tmp_path: Path) -> None:
        _write_spec(tmp_path, 120.4, 80.0)
        assert size_from_print_spec(tmp_path) == (120, 80)

    def test_missing_file(self, tmp_path: Path) -> None:
        assert size_from_print_spec(tmp_path) is None

    def test_malformed_json(self, tmp_path: Path) -> None:
        (tmp_path / "print_spec.json").write_text("not json", encoding="utf-8")
        assert size_from_print_spec(tmp_path) is None

    def test_missing_keys(self, tmp_path: Path) -> None:
        (tmp_path / "print_spec.json").write_text('{"dpi": 200}', encoding="utf-8")
        assert size_from_print_spec(tmp_path) is None

    def test_non_positive_rejected(self, tmp_path: Path) -> None:
        _write_spec(tmp_path, 0, 80)
        assert size_from_print_spec(tmp_path) is None


class TestDiscoverSourcesSpecFallback:
    """master.png（文件名无尺寸）应经同目录 print_spec.json 回退被发现。"""

    @staticmethod
    def _write_png(path: Path) -> None:
        from PIL import Image

        Image.new("RGB", (10, 10)).save(path)

    def test_master_png_discovered_via_spec(self, tmp_path: Path) -> None:
        self._write_png(tmp_path / "master.png")
        _write_spec(tmp_path, 120, 80)
        specs = discover_sources(tmp_path)
        assert [(s.path.name, s.width_cm, s.height_cm) for s in specs] == [
            ("master.png", 120, 80)
        ]

    def test_master_png_skipped_without_spec(self, tmp_path: Path) -> None:
        self._write_png(tmp_path / "master.png")
        assert discover_sources(tmp_path) == []

    def test_filename_size_wins_over_spec(self, tmp_path: Path) -> None:
        self._write_png(tmp_path / "200x80.png")
        _write_spec(tmp_path, 120, 80)
        specs = discover_sources(tmp_path)
        assert [(s.width_cm, s.height_cm) for s in specs] == [(200, 80)]
