"""GUI-side access to the baseline repository (active project resolution).

Bridges the Qt-free ``baseline`` domain layer to the desktop app: the store
lives under the per-user AppData dir (writable, survives app updates), the
active project is remembered via QSettings, and ``active_baseline_path`` falls
back to the bundled baseline when the store is unavailable. This is the single
GUI entry point; Phase B swaps the underlying repository for an HTTP one.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

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
