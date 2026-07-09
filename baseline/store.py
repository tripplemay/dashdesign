"""Filesystem-backed multi-project baseline repository (append-only versions).

Layout under ``root``::

    <root>/<baseline_id>/meta.json                 {baseline_id, name, active_version}
    <root>/<baseline_id>/versions/<version>.json   append-only baseline snapshots
    <root>/<baseline_id>/documents/<doc>           uploaded source documents

The repository is Qt-free and cloud-free; a Phase-B HTTP implementation can
satisfy the same surface so callers do not change.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from baseline import governance, versioning
from baseline.errors import BaselineError, GovernanceError, ValidationError
from baseline.schema import validation_errors

# 末尾用 \Z 而非 $：Python 的 $ 会匹配到结尾换行符之前，导致 "aicourse\n" 之类
# 带尾随换行的值被误判合法。cloud 端把本函数当作权威信任边界（不做规范化），必须严格。
_BASELINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,80}\Z")


def is_valid_baseline_id(value: str) -> bool:
    """True if ``value`` is directly storable as a baseline_id.

    The id doubles as a directory name, so it is kept to a lowercase slug
    (letter/digit first, then letters/digits/``-``/``_``, 3–81 chars) to stay
    consistent across case-sensitive and case-insensitive filesystems.
    """
    return bool(_BASELINE_ID_RE.match(value))


def normalize_baseline_id(raw: str) -> str:
    """Best-effort slugify user-entered text into the storable id form.

    Lowercases, collapses any run of unsupported characters into a single
    ``_``, and trims leading/trailing separators so the first character is a
    letter or digit. Already-valid ``-``/``_`` are preserved. Input without any
    usable latin/digit characters (e.g. all CJK/punctuation) normalizes to an
    empty or too-short string; callers should follow up with
    :func:`is_valid_baseline_id` rather than assume the result is storable.
    """
    slug = re.sub(r"[^a-z0-9_-]+", "_", raw.strip().lower())
    return slug.strip("-_")[:81]


def today_str() -> str:
    return datetime.now().strftime("%Y.%m.%d")


@dataclass(frozen=True)
class ProjectInfo:
    baseline_id: str
    name: str
    active_version: Optional[str]
    versions: List[str]


class BaselineRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- paths ---------------------------------------------------------
    def _project_dir(self, baseline_id: str) -> Path:
        return self.root / baseline_id

    def _versions_dir(self, baseline_id: str) -> Path:
        return self._project_dir(baseline_id) / "versions"

    def _documents_dir(self, baseline_id: str) -> Path:
        return self._project_dir(baseline_id) / "documents"

    def _meta_path(self, baseline_id: str) -> Path:
        return self._project_dir(baseline_id) / "meta.json"

    def version_path(self, baseline_id: str, version: str) -> Path:
        return self._versions_dir(baseline_id) / f"{version}.json"

    # -- projects ------------------------------------------------------
    def list_projects(self) -> List[ProjectInfo]:
        projects = []
        for meta_path in sorted(self.root.glob("*/meta.json")):
            baseline_id = meta_path.parent.name
            info = self.get_project(baseline_id)
            if info is not None:
                projects.append(info)
        return projects

    def get_project(self, baseline_id: str) -> Optional[ProjectInfo]:
        meta_path = self._meta_path(baseline_id)
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None  # 损坏的项目跳过，不阻断其余项目/启动播种
        return ProjectInfo(
            baseline_id=baseline_id,
            name=str(meta.get("name", baseline_id)),
            active_version=meta.get("active_version"),
            versions=self.list_versions(baseline_id),
        )

    def create_project(self, baseline: dict) -> ProjectInfo:
        baseline_id = str(baseline.get("baseline_id", ""))
        if not is_valid_baseline_id(baseline_id):
            raise BaselineError(f"非法 baseline_id：{baseline_id!r}")
        if self._meta_path(baseline_id).exists():
            raise BaselineError(f"项目已存在：{baseline_id}")
        self._require_valid(baseline)
        self._require_clean(baseline)  # 初始活跃版本即会喂给 C 端出图
        version = str(baseline["version"])
        self._versions_dir(baseline_id).mkdir(parents=True, exist_ok=True)
        self._documents_dir(baseline_id).mkdir(parents=True, exist_ok=True)
        self._write_json(self.version_path(baseline_id, version), baseline)
        name = str(baseline.get("project", {}).get("name") or baseline_id)
        self._write_meta(baseline_id, name=name, active_version=version)
        return self.get_project(baseline_id)  # type: ignore[return-value]

    # -- versions ------------------------------------------------------
    def list_versions(self, baseline_id: str) -> List[str]:
        vdir = self._versions_dir(baseline_id)
        if not vdir.exists():
            return []
        return versioning.sort_versions([p.stem for p in vdir.glob("*.json")])

    def load_version(self, baseline_id: str, version: str) -> dict:
        path = self.version_path(baseline_id, version)
        if not path.exists():
            raise BaselineError(f"版本不存在：{baseline_id}@{version}")
        return json.loads(path.read_text(encoding="utf-8"))

    def active_version(self, baseline_id: str) -> Optional[str]:
        info = self.get_project(baseline_id)
        return info.active_version if info else None

    def set_active_version(self, baseline_id: str, version: str) -> None:
        if not self.version_path(baseline_id, version).exists():
            raise BaselineError(f"版本不存在：{baseline_id}@{version}")
        # 活跃版本会喂给 to-C 出图，即便是草稿也必须通过治理检查。
        self._require_clean(self.load_version(baseline_id, version))
        info = self.get_project(baseline_id)
        name = info.name if info else baseline_id
        self._write_meta(baseline_id, name=name, active_version=version)

    def active_baseline_path(self, baseline_id: str) -> Optional[Path]:
        version = self.active_version(baseline_id)
        if not version:
            return None
        path = self.version_path(baseline_id, version)
        return path if path.exists() else None

    def new_draft(self, baseline_id: str, parent_version: str) -> dict:
        """Build (in memory) a new draft derived from ``parent_version``."""
        parent = self.load_version(baseline_id, parent_version)
        return versioning.new_draft_from(parent, today_str(), self.list_versions(baseline_id))

    def save_draft(self, baseline: dict) -> str:
        """Persist a draft snapshot (append-only). Returns the version string."""
        if baseline.get("status") != "draft":
            raise BaselineError("只能保存 status=draft 的版本")
        baseline_id = str(baseline.get("baseline_id", ""))
        if not self._meta_path(baseline_id).exists():
            raise BaselineError(f"项目不存在：{baseline_id}")
        self._require_valid(baseline)
        version = str(baseline["version"])
        target = self.version_path(baseline_id, version)
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing.get("status") != "draft":
                raise BaselineError(f"版本 {version} 已发布，不可覆盖；请新建草稿")
        self._write_json(target, baseline)
        return version

    def publish(self, baseline_id: str, version: str) -> dict:
        """Flip a draft to published (immutable) and make it the active version."""
        baseline = self.load_version(baseline_id, version)
        if baseline.get("status") == "published":
            return baseline
        if baseline.get("status") != "draft":
            raise BaselineError(f"只能发布草稿，当前状态：{baseline.get('status')}")
        self._require_valid(baseline)
        issues = governance.governance_issues(baseline)
        if issues:
            raise GovernanceError(issues)
        baseline["status"] = "published"
        self._write_json(self.version_path(baseline_id, version), baseline)
        self.set_active_version(baseline_id, version)
        return baseline

    # -- documents -----------------------------------------------------
    def add_document(self, baseline_id: str, src_path: Path) -> Path:
        src_path = Path(src_path)
        if not src_path.exists():
            raise BaselineError(f"文档不存在：{src_path}")
        docs = self._documents_dir(baseline_id)
        docs.mkdir(parents=True, exist_ok=True)
        dest = docs / src_path.name
        if dest.resolve() != src_path.resolve():
            shutil.copy2(src_path, dest)
        return dest

    # -- internals -----------------------------------------------------
    def _require_valid(self, baseline: dict) -> None:
        errors = validation_errors(baseline)
        if errors:
            raise ValidationError(errors)

    def _require_clean(self, baseline: dict) -> None:
        issues = governance.governance_issues(baseline)
        if issues:
            raise GovernanceError(issues)

    def _write_meta(self, baseline_id: str, name: str, active_version: Optional[str]) -> None:
        self._project_dir(baseline_id).mkdir(parents=True, exist_ok=True)
        self._write_json(
            self._meta_path(baseline_id),
            {"baseline_id": baseline_id, "name": name, "active_version": active_version},
        )

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
