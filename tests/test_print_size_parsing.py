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

from prepare_print_assets import parse_size_from_name  # noqa: E402

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
