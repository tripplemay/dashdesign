"""Wiring test for _superres_master in text_to_image_print.

The actual bug-2 fix is the swap to run_realesrgan_with_retry inside
_superres_master; this locks in that the retry helper is used and that an
exhausted retry still degrades gracefully to plain scaling (the pre-fix path).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import text_to_image_print as t2i  # noqa: E402


def _small_master(tmp_path: Path) -> Path:
    master = tmp_path / "master.png"
    Image.new("RGB", (100, 100), (10, 20, 30)).save(master)
    return master


def _existing_binary_and_models(tmp_path: Path) -> tuple[Path, Path]:
    binary = tmp_path / "realesrgan.exe"
    binary.write_bytes(b"x")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    return binary, model_dir


class TestSuperresMasterWiring:
    def test_uses_retry_helper_and_reports_sr_applied(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        master = _small_master(tmp_path)
        binary, model_dir = _existing_binary_and_models(tmp_path)
        calls: list[tuple[Path, Path]] = []

        def _fake_retry(src: Path, dest: Path, *args: object, **kwargs: object) -> None:
            calls.append((src, dest))

        monkeypatch.setattr(t2i, "run_realesrgan_with_retry", _fake_retry)
        path, applied = t2i._superres_master(
            master, (400, 400), binary, model_dir, "realesrgan-x4plus"
        )
        assert applied is True
        assert path == master.parent / "_sr" / "master_realesrgan_x4.png"
        assert len(calls) == 1

    def test_falls_back_to_plain_scaling_when_retry_exhausted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        master = _small_master(tmp_path)
        binary, model_dir = _existing_binary_and_models(tmp_path)

        def _boom(*args: object, **kwargs: object) -> None:
            raise subprocess.CalledProcessError(returncode=3221225477, cmd=["realesrgan"])

        monkeypatch.setattr(t2i, "run_realesrgan_with_retry", _boom)
        path, applied = t2i._superres_master(
            master, (400, 400), binary, model_dir, "realesrgan-x4plus"
        )
        assert applied is False
        assert path == master  # fell back to the original master for plain Lanczos
