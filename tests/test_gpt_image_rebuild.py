"""Unit tests for physical-size resolution in the GPT image rebuild worker.

Regression coverage for chaining text-to-image / full-poster output
(``master.png``, whose filename carries no size) into the GPT rebuild worker.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ is not a package and not on the default test path; the worker adds it
# at runtime (runpy from the scripts dir). Mirror that here to import the module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import gpt_image_rebuild as rebuild  # noqa: E402


def _write_print_spec(directory: Path, width_cm: float, height_cm: float) -> None:
    (directory / "print_spec.json").write_text(
        json.dumps({"width_cm": width_cm, "height_cm": height_cm, "dpi": 200}),
        encoding="utf-8",
    )


class TestResolvePhysicalSize:
    def test_size_token_in_filename_wins(self, tmp_path: Path) -> None:
        source = tmp_path / "美业海报_80乘80.png"
        source.touch()
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_falls_back_to_sibling_print_spec(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        _write_print_spec(tmp_path, 80.0, 80.0)
        # 80.0 in the spec is normalized to the int 80 to match filename parsing.
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_print_spec_preferred_over_directory_name(self, tmp_path: Path) -> None:
        workdir = tmp_path / "20260708_184744_60x90_background_t2i"
        workdir.mkdir()
        source = workdir / "master.png"
        source.touch()
        _write_print_spec(workdir, 80.0, 80.0)
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_falls_back_to_ancestor_directory_name(self, tmp_path: Path) -> None:
        workdir = tmp_path / "20260708_184744_80x80_background_t2i"
        workdir.mkdir()
        source = workdir / "master.png"
        source.touch()
        assert rebuild.resolve_physical_size(source) == (80, 80)

    def test_non_integer_cm_preserved(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        _write_print_spec(tmp_path, 21.0, 29.7)
        assert rebuild.resolve_physical_size(source) == (21, 29.7)

    def test_malformed_print_spec_ignored(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        (tmp_path / "print_spec.json").write_text("{not json", encoding="utf-8")
        assert rebuild.resolve_physical_size(source) is None

    def test_non_positive_size_ignored(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        _write_print_spec(tmp_path, 0, 80)
        assert rebuild.resolve_physical_size(source) is None

    def test_returns_none_when_unresolvable(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        assert rebuild.resolve_physical_size(source) is None


class TestBuildProfileError:
    def test_raises_clear_error_when_size_unresolvable(self, tmp_path: Path) -> None:
        source = tmp_path / "master.png"
        source.touch()
        with pytest.raises(ValueError, match="无法确定物理尺寸"):
            rebuild.build_profile(source, print_dpi=200)
