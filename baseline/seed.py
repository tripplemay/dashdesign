"""Seed the baseline store from the bundled default baseline on first run."""

from __future__ import annotations

import json

from app_runtime import baseline_path
from baseline.store import BaselineRepository


def seed_if_empty(repo: BaselineRepository) -> None:
    """Create the default project from the bundled baseline if the store is empty."""
    if repo.list_projects():
        return
    bundled = baseline_path()
    if not bundled.exists():
        return
    try:
        baseline = json.loads(bundled.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    try:
        repo.create_project(baseline)
    except Exception:  # noqa: BLE001 - 播种失败不应阻止应用启动
        pass
