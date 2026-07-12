"""Unit tests for the Real-ESRGAN small-tile retry in prepare_print_assets.

A Real-ESRGAN crash on Windows surfaces as ``CalledProcessError`` with the
access-violation return code 3221225477 (0xC0000005) — typically GPU VRAM
pressure or a flaky driver on a large tile. The retry shrinks the tile before
letting the caller fall back to plain scaling.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

# scripts/ is not a package and not on the default test path; mirror the runtime
# path shim the worker uses so we can import (and monkeypatch) the module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import prepare_print_assets as ppa  # noqa: E402

_ACCESS_VIOLATION = 3221225477  # 0xC0000005


def _tile_of(cmd: list[str]) -> int:
    return int(cmd[cmd.index("-t") + 1])


class _Recorder:
    """Fake subprocess.run that fails on the given tile sizes, records order."""

    def __init__(self, fail_tiles: set[int]) -> None:
        self.fail_tiles = fail_tiles
        self.tiles: list[int] = []

    def run(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        tile = _tile_of(cmd)
        self.tiles.append(tile)
        if tile in self.fail_tiles:
            raise subprocess.CalledProcessError(returncode=_ACCESS_VIOLATION, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    return (
        tmp_path / "in.png",
        tmp_path / "out.png",
        tmp_path / "realesrgan.exe",
        tmp_path / "models",
    )


class TestRunRealesrganWithRetry:
    def test_falls_back_to_smaller_tile_on_access_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _Recorder(fail_tiles={512})
        monkeypatch.setattr(ppa.subprocess, "run", rec.run)
        source, dest, binary, model_dir = _paths(tmp_path)
        ppa.run_realesrgan_with_retry(source, dest, binary, model_dir)
        assert rec.tiles == [512, 256]

    def test_reraises_after_all_tiles_exhausted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _Recorder(fail_tiles={512, 256, 128})
        monkeypatch.setattr(ppa.subprocess, "run", rec.run)
        source, dest, binary, model_dir = _paths(tmp_path)
        with pytest.raises(subprocess.CalledProcessError):
            ppa.run_realesrgan_with_retry(source, dest, binary, model_dir)
        assert rec.tiles == [512, 256, 128]

    def test_first_tile_success_does_not_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _Recorder(fail_tiles=set())
        monkeypatch.setattr(ppa.subprocess, "run", rec.run)
        source, dest, binary, model_dir = _paths(tmp_path)
        ppa.run_realesrgan_with_retry(source, dest, binary, model_dir)
        assert rec.tiles == [512]

    def test_timeout_is_not_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def _run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
            calls.append(cmd)
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=900)

        monkeypatch.setattr(ppa.subprocess, "run", _run)
        source, dest, binary, model_dir = _paths(tmp_path)
        with pytest.raises(subprocess.TimeoutExpired):
            ppa.run_realesrgan_with_retry(source, dest, binary, model_dir)
        assert len(calls) == 1


class TestFitWithin:
    """等比缩放到适应目标框：无填充、无裁切、无变形（修复批量印刷"AI 补边"）。"""

    def test_portrait_into_landscape_keeps_aspect_no_padding(self) -> None:
        img = Image.new("RGB", (300, 400))  # 3:4 portrait
        fitted, size = ppa.fit_within(img, (1000, 500))  # landscape target
        assert size == (375, 500)  # scale=1.25 (fit height), aspect 3:4 preserved
        assert fitted.size == (375, 500)  # output IS the fitted image, not the target box

    def test_upscales_small_image_proportionally(self) -> None:
        img = Image.new("RGB", (100, 100))
        fitted, size = ppa.fit_within(img, (500, 500))
        assert size == (500, 500)

    def test_matching_aspect_fills_target(self) -> None:
        img = Image.new("RGB", (200, 100))  # 2:1
        _, size = ppa.fit_within(img, (1000, 500))  # 2:1
        assert size == (1000, 500)


class TestProcessOneLayout:
    def _spec(self, tmp_path: Path, w_px: int, h_px: int, w_cm: int, h_cm: int) -> "ppa.SourceSpec":
        path = tmp_path / f"{w_cm}乘以{h_cm}_海报.jpg"
        Image.new("RGB", (w_px, h_px), (120, 130, 140)).save(path)
        return ppa.SourceSpec(
            path=path, width_cm=w_cm, height_cm=h_cm, source_width=w_px, source_height=h_px
        )

    def test_aspect_mismatch_uses_proportional_not_blurred_border(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        # portrait source, landscape declared size -> big aspect mismatch
        spec = self._spec(tmp_path, 600, 900, 200, 80)
        row = ppa.process_one(spec, out, dpi=100, aspect_tolerance=1.5)
        assert row["layout"] == "proportional"  # NOT centered_with_blurred_background
        # output == the proportionally fitted image, no padding to the target box
        assert row["output_px"] == row["content_px"]
        with Image.open(out / spec.path.name) as saved:
            assert f"{saved.size[0]}x{saved.size[1]}" == row["output_px"]
            # source aspect (2:3) preserved -> no distortion, no border
            assert abs(saved.size[0] / saved.size[1] - 600 / 900) < 0.01

    def test_matching_aspect_resizes_to_exact_size(self, tmp_path: Path) -> None:
        out = tmp_path / "out2"
        out.mkdir()
        spec = self._spec(tmp_path, 500, 200, 200, 80)  # 2.5:1 == 200x80
        row = ppa.process_one(spec, out, dpi=100, aspect_tolerance=1.5)
        assert row["layout"] == "resized"
