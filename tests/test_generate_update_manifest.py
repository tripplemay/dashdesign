"""Tests for the release update-manifest generator (size + sha256 fields)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "generate_update_manifest.py"


def _run(*args: str) -> None:
    subprocess.run([sys.executable, str(SCRIPT), *args], check=True)


def test_manifest_includes_size_and_sha256(tmp_path: Path) -> None:
    artifact = tmp_path / "DashDesign-0.2.0-windows-setup.exe"
    artifact.write_bytes(b"fake installer payload" * 100)
    output = tmp_path / "update-manifest.json"

    _run(
        "--version",
        "v0.2.0",
        "--notes",
        "test",
        "--windows-url",
        "https://example/DashDesign-0.2.0-windows-setup.exe",
        "--windows-file",
        str(artifact),
        "--output",
        str(output),
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["version"] == "0.2.0"  # v-prefix stripped
    windows = manifest["platforms"]["windows"]
    assert windows["url"].endswith("windows-setup.exe")
    assert windows["size"] == artifact.stat().st_size
    assert windows["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()


def test_missing_file_omits_size(tmp_path: Path) -> None:
    output = tmp_path / "update-manifest.json"
    _run(
        "--version",
        "0.2.0",
        "--windows-url",
        "https://example/x.exe",
        "--output",
        str(output),
    )
    windows = json.loads(output.read_text(encoding="utf-8"))["platforms"]["windows"]
    assert "size" not in windows
    assert "sha256" not in windows
