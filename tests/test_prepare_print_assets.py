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
