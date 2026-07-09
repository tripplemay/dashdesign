"""Writable default output locations for the workflow pages.

A packaged build installs under a read-only directory (Program Files on
Windows), so workflow outputs must default to a per-user writable location —
alongside the baseline cache under the AppData dir — instead of the app root.
Dev runs keep writing under the project root for easy inspection.

The read-only detection and migration logic lives in the Qt-free
``app_runtime`` module (unit-tested); this thin layer only supplies the base
directory via ``QStandardPaths``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths

from app_runtime import PROJECT_ROOT, is_packaged, resolve_output_dir


def output_base() -> Path:
    """Root under which a fresh install writes workflow outputs."""
    if is_packaged():
        location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        if location:
            return Path(location) / "output"
    return PROJECT_ROOT


def default_output(*parts: str) -> str:
    """Writable default output dir (as text) for a fresh install."""
    return str(output_base().joinpath(*parts))


def restore_output(saved: str, *parts: str) -> str:
    """Return ``saved``, migrated to the writable default if it is empty or
    points inside the read-only install tree."""
    return resolve_output_dir(saved, output_base(), *parts)
