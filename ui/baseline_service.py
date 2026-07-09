"""GUI-side access to the baseline repository (active project resolution).

Bridges the Qt-free ``baseline`` domain layer to the desktop app: the store
lives under the per-user AppData dir (writable, survives app updates), the
active project is remembered via QSettings, and ``active_baseline_path`` falls
back to the bundled baseline when the store is unavailable. This is the single
GUI entry point; Phase B swaps the underlying repository for an HTTP one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSettings, QStandardPaths

from app_runtime import baseline_path as bundled_baseline_path
from baseline.seed import seed_if_empty
from baseline.store import BaselineRepository, ProjectInfo, VersionSummary
from ui import cloud_bootstrap

_ACTIVE_KEY = "baseline/active_project"
# Either a local BaselineRepository or a cloud HttpBaselineRepository (same
# duck-typed surface); chosen by whether a cloud endpoint + token are configured.
_repo: Optional[Any] = None


def _store_root() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / "baselines"


def _cloud_cache_root() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / "baseline_cloud_cache"


def repository() -> Any:
    global _repo
    if _repo is None:
        if cloud_bootstrap.is_configured():
            # Cloud mode (the internal-tool default): the baked-in endpoint +
            # shared token mean ordinary users configure nothing. The server owns
            # seeding + governance; no local seed.
            from cloud.client import HttpBaselineRepository

            _repo = HttpBaselineRepository(
                cloud_bootstrap.baseline_endpoint(), cloud_bootstrap.client_token(), _cloud_cache_root()
            )
        else:
            _repo = BaselineRepository(_store_root())
            seed_if_empty(_repo)
    return _repo


def reset_repository() -> None:
    """Drop the cached repository so a changed cloud config takes effect."""
    global _repo
    _repo = None


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


def _stored_active_project() -> str:
    return str(QSettings().value(_ACTIVE_KEY, "") or "")


@dataclass(frozen=True)
class BaselineOverview:
    """Everything the baseline page renders on load, fetched in the fewest calls."""

    projects: List[ProjectInfo]
    active_project_id: Optional[str]
    versions: List[VersionSummary]
    active_version: Optional[str]
    selected_version: Optional[str]
    selected_payload: Optional[Dict[str, Any]]


def _resolve_active_pid(ids: List[str], selected: Optional[str], stored: str) -> Optional[str]:
    if selected in ids:
        return selected
    if stored in ids:
        return stored
    return ids[0] if ids else None


def load_overview(
    selected_project: Optional[str] = None,
    selected_version: Optional[str] = None,
) -> BaselineOverview:
    """Aggregate the baseline page's data in the fewest requests.

    Collapses the previously repeated ``/projects`` and ``active_version`` calls
    into one ``list_projects`` (each ProjectInfo already carries its
    ``active_version``), and reads per-version status via ``list_version_summaries``
    instead of downloading every version. Only the selected version's full payload
    is fetched. Safe to call off the UI thread.
    """
    repo = repository()
    projects = repo.list_projects()
    ids = [p.baseline_id for p in projects]
    pid = _resolve_active_pid(ids, selected_project, _stored_active_project())
    info = next((p for p in projects if p.baseline_id == pid), None)
    active_version = info.active_version if info else None
    versions: List[VersionSummary] = []
    selected: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    if pid:
        versions = repo.list_version_summaries(pid)
        available = [v.version for v in versions]
        if selected_version in available:
            selected = selected_version
        elif available:
            selected = available[-1]  # 默认选最新版本（available 升序），与旧“默认最新”行为一致
        if selected:
            payload = repo.load_version(pid, selected)
    return BaselineOverview(
        projects=projects,
        active_project_id=pid,
        versions=versions,
        active_version=active_version,
        selected_version=selected,
        selected_payload=payload,
    )


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
