"""One-way recovery of legacy desktop baselines into the cloud repository."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from baseline import versioning
from baseline.errors import BaselineError
from baseline.store import BaselineRepository, ProjectInfo


@dataclass(frozen=True)
class LegacyProject:
    baseline_id: str
    active_version: str
    versions: List[Dict[str, Any]]
    source: str


@dataclass
class MigrationSummary:
    migrated: List[str] = field(default_factory=list)
    already_present: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.migrated)

    def message(self) -> str:
        parts: List[str] = []
        if self.migrated:
            parts.append(f"已从本机恢复 {len(self.migrated)} 个项目到云端")
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} 个同名项目与云端不一致，未自动覆盖")
        if self.errors:
            parts.append(f"{len(self.errors)} 个项目恢复失败，本机原文件仍保留")
        return "；".join(parts)


def _from_local_store(root: Path) -> List[LegacyProject]:
    if not root.is_dir():
        return []
    repo = BaselineRepository(root)
    projects: List[LegacyProject] = []
    for info in repo.list_projects():
        if not info.active_version or info.active_version not in info.versions:
            continue
        try:
            payloads = [repo.load_version(info.baseline_id, version) for version in info.versions]
        except (BaselineError, OSError, ValueError):
            continue
        projects.append(
            LegacyProject(info.baseline_id, info.active_version, payloads, "local")
        )
    return projects


def _read_active_marker(project_dir: Path) -> Optional[str]:
    try:
        value = json.loads((project_dir / "active.json").read_text(encoding="utf-8"))
        return str(value.get("active") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def _from_cloud_cache(root: Path) -> List[LegacyProject]:
    """Reconstruct projects from the cache used by older cloud clients.

    The cache intentionally has no meta.json, so the active marker and version
    payloads are the only recovery material available after a fresh server is
    accidentally deployed.
    """
    if not root.is_dir():
        return []
    projects: List[LegacyProject] = []
    for project_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        payloads: List[Dict[str, Any]] = []
        versions_dir = project_dir / "versions"
        for path in sorted(versions_dir.glob("*.json")) if versions_dir.is_dir() else []:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("baseline_id", "")) != project_dir.name:
                continue
            if str(payload.get("version", "")) != path.stem:
                continue
            payloads.append(payload)
        ordered = versioning.sort_versions([str(item.get("version", "")) for item in payloads])
        by_version = {str(item["version"]): item for item in payloads}
        active = _read_active_marker(project_dir)
        if active not in by_version:
            active = ordered[-1] if ordered else None
        if active:
            projects.append(
                LegacyProject(
                    project_dir.name,
                    active,
                    [by_version[version] for version in ordered],
                    "cache",
                )
            )
    return projects


def _matches_remote(project: LegacyProject, remote: ProjectInfo, cloud_repo: Any) -> bool:
    local_by_version = {str(item["version"]): item for item in project.versions}
    if not set(local_by_version).issubset(remote.versions):
        return False
    try:
        return all(
            cloud_repo.load_version(project.baseline_id, version) == payload
            for version, payload in local_by_version.items()
        )
    except Exception:  # noqa: BLE001 - a failed comparison must never overwrite cloud data
        return False


def migrate_legacy_projects(
    local_roots: Iterable[Path],
    cloud_cache_roots: Iterable[Path],
    cloud_repo: Any,
    remote_projects: Iterable[ProjectInfo],
) -> MigrationSummary:
    """Copy legacy projects to cloud without modifying either local source.

    Filesystem projects are considered authoritative upgrade input. Cached cloud
    snapshots are used only when the remote repository is completely empty,
    which avoids resurrecting stale cache entries during normal operation.
    Every project is imported in one server transaction through import_project.
    """
    summary = MigrationSummary()
    remote_by_id = {item.baseline_id: item for item in remote_projects}
    candidates: Dict[str, LegacyProject] = {}
    for root in local_roots:
        for item in _from_local_store(Path(root)):
            candidates.setdefault(item.baseline_id, item)
    if not remote_by_id:
        for root in cloud_cache_roots:
            for item in _from_cloud_cache(Path(root)):
                candidates.setdefault(item.baseline_id, item)

    for baseline_id, project in candidates.items():
        remote = remote_by_id.get(baseline_id)
        if remote is not None:
            if _matches_remote(project, remote, cloud_repo):
                summary.already_present.append(baseline_id)
            elif project.source == "local":
                summary.conflicts.append(baseline_id)
            continue
        try:
            created = cloud_repo.import_project(project.versions, project.active_version)
        except Exception as exc:  # noqa: BLE001 - keep other recoverable projects moving
            summary.errors.append(f"{baseline_id}: {exc}")
            continue
        summary.migrated.append(created.baseline_id)
        remote_by_id[created.baseline_id] = created
    return summary
