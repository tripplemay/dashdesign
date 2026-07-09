#!/usr/bin/env python3
"""Runtime helpers shared by the DashDesign GUI and worker processes.

This module must stay free of Qt imports: the packaged DashDesignWorker
executable and the ``--worker`` code path import it to run workflow scripts
without loading PySide6.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from typing import Iterable, List

PROJECT_ROOT = Path(__file__).resolve().parent
APP_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve()
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
PYTHON = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
BASELINE_RELATIVE_PATH = Path("docs") / "baseline" / "baseline.v1.draft.json"
BASELINE_SCHEMA_RELATIVE_PATH = Path("docs") / "baseline" / "baseline.schema.json"
PROMPT_TEMPLATE_RELATIVE_PATH = Path("docs") / "prompt_templates" / "full_poster_templates.v1.json"
SCENE_PROMPTS_RELATIVE_PATH = Path("docs") / "prompt_templates" / "scene_prompts.v1.json"


def read_app_version() -> str:
    for candidate in (APP_ROOT / "VERSION", PROJECT_ROOT / "VERSION"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value.lstrip("v")
    return os.environ.get("DASHDESIGN_VERSION", "0.1.0").lstrip("v")


def configured_update_manifest_url() -> str:
    env_url = os.environ.get("DASHDESIGN_UPDATE_MANIFEST_URL", "").strip()
    if env_url:
        return env_url
    for candidate in (APP_ROOT / "UPDATE_MANIFEST_URL", PROJECT_ROOT / "UPDATE_MANIFEST_URL"):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


APP_VERSION = read_app_version()


def is_packaged() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_roots() -> List[Path]:
    """Read-only roots of a packaged install; empty for dev runs (writable).

    A packaged build lives under a read-only dir (Program Files on Windows), so
    output paths that resolve inside it cannot be created. Dev runs keep writing
    under the project root, which is writable.
    """
    if not is_packaged():
        return []
    roots = {APP_ROOT, PROJECT_ROOT}
    try:
        roots.add(Path(sys.executable).resolve().parent)
    except (OSError, ValueError):
        pass
    return list(roots)


def path_is_within(path: str, roots: Iterable[Path]) -> bool:
    """True when ``path`` resolves to a location inside any of ``roots``."""
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            resolved.relative_to(Path(root).resolve())
            return True
        except ValueError:
            continue
    return False


def resolve_output_dir(saved: str, base: Path, *parts: str) -> str:
    """Return ``saved`` unless it is empty or inside the packaged read-only
    install tree, in which case fall back to ``base/<parts>`` (a writable
    per-user default). This both seeds fresh installs and migrates a stale path
    a previous build persisted under the (now read-only) install dir.
    """
    if saved and not path_is_within(saved, install_roots()):
        return saved
    return str(base.joinpath(*parts))


def worker_prefix() -> list[str]:
    if is_packaged():
        executable_dir = Path(sys.executable).resolve().parent
        worker_name = "DashDesignWorker.exe" if sys.platform.startswith("win") else "DashDesignWorker"
        worker_executable = executable_dir / worker_name
        if worker_executable.exists():
            return [str(worker_executable), "--worker"]
        return [str(Path(sys.executable).resolve()), "--worker"]
    return [PYTHON, str(PROJECT_ROOT / "desktop_qt_app.py"), "--worker"]


def runtime_root() -> Path:
    if is_packaged():
        return APP_ROOT
    return PROJECT_ROOT


def runtime_tool_path() -> Path:
    name = "realesrgan-ncnn-vulkan.exe" if sys.platform.startswith("win") else "realesrgan-ncnn-vulkan"
    return runtime_root() / "tools" / name


def runtime_model_dir() -> Path:
    return runtime_root() / "tools" / "models"


def version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.strip().lstrip("v").split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits or "0"))
    return tuple(parts)


def platform_key() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def baseline_path() -> Path:
    bundled = APP_ROOT / BASELINE_RELATIVE_PATH
    if bundled.exists():
        return bundled
    return PROJECT_ROOT / BASELINE_RELATIVE_PATH


def baseline_schema_path() -> Path:
    bundled = APP_ROOT / BASELINE_SCHEMA_RELATIVE_PATH
    if bundled.exists():
        return bundled
    return PROJECT_ROOT / BASELINE_SCHEMA_RELATIVE_PATH


def prompt_template_library_path() -> Path:
    bundled = APP_ROOT / PROMPT_TEMPLATE_RELATIVE_PATH
    if bundled.exists():
        return bundled
    return PROJECT_ROOT / PROMPT_TEMPLATE_RELATIVE_PATH


def scene_prompts_library_path() -> Path:
    bundled = APP_ROOT / SCENE_PROMPTS_RELATIVE_PATH
    if bundled.exists():
        return bundled
    return PROJECT_ROOT / SCENE_PROMPTS_RELATIVE_PATH


def evidenced_text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("text", "")).strip()
    return str(value or "").strip()


def text_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    output: list[str] = []
    for value in values:
        text = evidenced_text(value)
        if text:
            output.append(text)
    return output


def first_image(directory: Path) -> "Path | None":
    if not directory.exists() or not directory.is_dir():
        return None
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            return path
    return None


def first_output_image(directory: Path) -> "Path | None":
    review = directory / "review" / "contact_sheet.jpg"
    if review.exists():
        return review
    direct = first_image(directory)
    if direct is not None:
        return direct
    review_dir = directory / "review"
    review_image = first_image(review_dir)
    if review_image is not None:
        return review_image
    if not directory.exists() or not directory.is_dir():
        return None
    candidates = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def run_script_worker(script_name: str, args: list[str]) -> int:
    script_map = {
        "batch-style": runtime_root() / "scripts" / "batch_style_preserved_print.py",
        "batch-pil": runtime_root() / "scripts" / "prepare_print_assets.py",
        "text-image": runtime_root() / "scripts" / "text_to_image_print.py",
        "full-poster": runtime_root() / "scripts" / "full_poster_image2.py",
        "gpt": runtime_root() / "scripts" / "gpt_image_rebuild.py",
        "qr": runtime_root() / "scripts" / "remove_qr_area.py",
    }
    script_path = script_map.get(script_name)
    if script_path is None:
        print(f"Unknown worker: {script_name}", file=sys.stderr)
        return 2
    if not script_path.exists():
        print(f"Worker script not found: {script_path}", file=sys.stderr)
        return 2

    old_argv = sys.argv[:]
    old_path = sys.path[:]
    sys.argv = [str(script_path), *args]
    sys.path.insert(0, str(script_path.parent))
    # worker 的 stdout 连的是管道（非 TTY），CPython 默认全块缓冲，会把脚本
    # 的进度输出憋到缓冲满或退出才冲出。切成行缓冲让每行 print 立即到达 GUI。
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    finally:
        sys.argv = old_argv
        sys.path = old_path
    return 0
