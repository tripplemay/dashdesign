"""GUI-side access to the baseline repository (active project resolution).

Bridges the Qt-free ``baseline`` domain layer to the desktop app: the store
lives under the per-user AppData dir (writable, survives app updates), the
active project is remembered via QSettings, and ``active_baseline_path`` falls
back to the bundled baseline when the store is unavailable. This is the single
GUI entry point; Phase B swaps the underlying repository for an HTTP one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSettings, QStandardPaths

from app_runtime import baseline_path as bundled_baseline_path
from baseline.seed import seed_if_empty
from baseline.store import BaselineRepository, ProjectInfo

_ACTIVE_KEY = "baseline/active_project"
_repo: Optional[BaselineRepository] = None


def _store_root() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / "baselines"


def repository() -> BaselineRepository:
    global _repo
    if _repo is None:
        _repo = BaselineRepository(_store_root())
        seed_if_empty(_repo)
    return _repo


def projects() -> List[ProjectInfo]:
    return repository().list_projects()


def active_project_id() -> Optional[str]:
    ids = [p.baseline_id for p in projects()]
    stored = str(QSettings().value(_ACTIVE_KEY, "") or "")
    if stored in ids:
        return stored
    return ids[0] if ids else None


def set_active_project(baseline_id: str) -> None:
    QSettings().setValue(_ACTIVE_KEY, baseline_id)


def active_baseline_path() -> Path:
    repo = repository()
    project_id = active_project_id()
    if project_id:
        path = repo.active_baseline_path(project_id)
        if path is not None:
            return path
    return bundled_baseline_path()


def bundled_baseline() -> Dict[str, Any]:
    """The bundled default baseline, used as the "从内置模板" source."""
    return json.loads(bundled_baseline_path().read_text(encoding="utf-8"))


def load_project_baseline(baseline_id: str) -> Dict[str, Any]:
    """The active version of an existing project, used as a clone source."""
    repo = repository()
    version = repo.active_version(baseline_id)
    if not version:
        raise ValueError(f"项目无可用版本：{baseline_id}")
    return repo.load_version(baseline_id, version)


def create_project(baseline: Dict[str, Any]) -> ProjectInfo:
    """Create a project from a prepared baseline and make it active."""
    info = repository().create_project(baseline)
    set_active_project(info.baseline_id)
    return info
