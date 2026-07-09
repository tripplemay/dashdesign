"""Server-authoritative baseline persistence over SQLAlchemy.

Mirrors the domain semantics of ``baseline.store.BaselineRepository`` (append-only
versions, draft-overwrite rules, published-immutable, schema + governance gates)
but backed by relational storage and extended with optimistic concurrency
(ETag / If-Match). Schema validation and B->C governance are reused verbatim from
the Qt-free ``baseline`` domain so a patched desktop client cannot bypass them.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from baseline import governance, versioning
from baseline.errors import BaselineError, GovernanceError, ValidationError
from baseline.schema import validation_errors
from baseline.store import _BASELINE_ID_RE, today_str

from cloud.server import db
from cloud.server.docstore import DocumentStore, content_hash


class ConflictError(BaselineError):
    """Optimistic-lock / uniqueness conflict -> HTTP 409."""


class NotFoundError(BaselineError):
    """Missing project/version/job -> HTTP 404."""


def canonical_etag(data: dict) -> str:
    """Stable content hash used as the version ETag (order-independent)."""
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class SqlBaselineStore:
    """Domain operations bound to a single request-scoped Session."""

    def __init__(self, session: Session, doc_store: DocumentStore) -> None:
        self.s = session
        self.doc_store = doc_store

    # -- validation gates (reused domain logic) ------------------------
    def _require_valid(self, baseline: dict) -> None:
        errors = validation_errors(baseline)
        if errors:
            raise ValidationError(errors)

    def _require_clean(self, baseline: dict) -> None:
        issues = governance.governance_issues(baseline)
        if issues:
            raise GovernanceError(issues)

    # -- projects ------------------------------------------------------
    def list_projects(self, org_id: Optional[str] = None) -> List[db.Project]:
        stmt = select(db.Project)
        if org_id is not None:
            stmt = stmt.where(db.Project.org_id == org_id)
        return list(self.s.scalars(stmt.order_by(db.Project.baseline_id)))

    def get_project(self, baseline_id: str) -> Optional[db.Project]:
        return self.s.get(db.Project, baseline_id)

    def list_versions(self, baseline_id: str) -> List[str]:
        rows = self.s.scalars(
            select(db.Version.version).where(db.Version.baseline_id == baseline_id)
        )
        return versioning.sort_versions(list(rows))

    def _get_version_row(self, baseline_id: str, version: str) -> Optional[db.Version]:
        return self.s.scalar(
            select(db.Version).where(
                db.Version.baseline_id == baseline_id, db.Version.version == version
            )
        )

    def create_project(self, baseline: dict, owner_user_id: str, org_id: str = "default") -> db.Project:
        baseline_id = str(baseline.get("baseline_id", ""))
        if not _BASELINE_ID_RE.match(baseline_id):
            raise BaselineError(f"非法 baseline_id：{baseline_id!r}")
        if self.get_project(baseline_id) is not None:
            raise ConflictError(f"项目已存在：{baseline_id}")
        self._require_valid(baseline)
        self._require_clean(baseline)  # initial active version feeds to-C generation
        version = str(baseline["version"])
        name = str(baseline.get("project", {}).get("name") or baseline_id)

        project = db.Project(
            baseline_id=baseline_id, name=name, active_version=version, org_id=org_id
        )
        self.s.add(project)
        self.s.add(
            db.Version(
                baseline_id=baseline_id,
                version=version,
                status=str(baseline.get("status", "draft")),
                parent_version=baseline.get("parent_version"),
                etag=canonical_etag(baseline),
                data=baseline,
            )
        )
        self.s.add(
            db.Membership(baseline_id=baseline_id, user_id=owner_user_id, role="admin")
        )
        self.s.flush()
        return project

    def load_version(self, baseline_id: str, version: str) -> Tuple[dict, str]:
        row = self._get_version_row(baseline_id, version)
        if row is None:
            raise NotFoundError(f"版本不存在：{baseline_id}@{version}")
        return row.data, row.etag

    def active_version(self, baseline_id: str) -> Optional[str]:
        project = self.get_project(baseline_id)
        return project.active_version if project else None

    def set_active_version(self, baseline_id: str, version: str) -> None:
        row = self._get_version_row(baseline_id, version)
        if row is None:
            raise NotFoundError(f"版本不存在：{baseline_id}@{version}")
        # The active version feeds to-C image generation; even a draft must pass.
        self._require_clean(row.data)
        project = self.get_project(baseline_id)
        if project is None:
            raise NotFoundError(f"项目不存在：{baseline_id}")
        project.active_version = version
        self.s.flush()

    def new_draft(self, baseline_id: str, parent_version: str) -> dict:
        parent, _ = self.load_version(baseline_id, parent_version)
        return versioning.new_draft_from(parent, today_str(), self.list_versions(baseline_id))

    def save_draft(self, baseline: dict, if_match: Optional[str] = None) -> Tuple[str, str]:
        """Append-only draft persist with optimistic locking. Returns (version, etag)."""
        if baseline.get("status") != "draft":
            raise BaselineError("只能保存 status=draft 的版本")
        baseline_id = str(baseline.get("baseline_id", ""))
        if self.get_project(baseline_id) is None:
            raise NotFoundError(f"项目不存在：{baseline_id}")
        self._require_valid(baseline)
        version = str(baseline["version"])
        etag = canonical_etag(baseline)
        existing = self._get_version_row(baseline_id, version)
        if existing is not None:
            if existing.status != "draft":
                raise ConflictError(f"版本 {version} 已发布，不可覆盖；请新建草稿")
            # Overwriting an existing draft is server-authoritative optimistic
            # locking: the caller MUST hold the current ETag. A missing If-Match
            # (blind overwrite, e.g. two operators who created the same version
            # the same day) is itself a conflict — never a silent last-writer-win.
            if if_match is None or if_match != existing.etag:
                raise ConflictError(
                    f"版本 {version} 已被他人修改或未携带 ETag，请刷新后重试"
                )
            existing.data = baseline
            existing.etag = etag
            existing.parent_version = baseline.get("parent_version")
        else:
            self.s.add(
                db.Version(
                    baseline_id=baseline_id,
                    version=version,
                    status="draft",
                    parent_version=baseline.get("parent_version"),
                    etag=etag,
                    data=baseline,
                )
            )
        self.s.flush()
        return version, etag

    def publish(self, baseline_id: str, version: str) -> dict:
        row = self._get_version_row(baseline_id, version)
        if row is None:
            raise NotFoundError(f"版本不存在：{baseline_id}@{version}")
        if row.status == "published":
            return row.data
        if row.status != "draft":
            raise BaselineError(f"只能发布草稿，当前状态：{row.status}")
        self._require_valid(row.data)
        issues = governance.governance_issues(row.data)
        if issues:
            raise GovernanceError(issues)
        published = dict(row.data)
        published["status"] = "published"
        row.data = published
        row.status = "published"
        row.etag = canonical_etag(published)
        project = self.get_project(baseline_id)
        if project is not None:
            project.active_version = version
        self.s.flush()
        return published

    # -- documents -----------------------------------------------------
    def add_document(self, baseline_id: str, filename: str, data: bytes) -> db.Document:
        if self.get_project(baseline_id) is None:
            raise NotFoundError(f"项目不存在：{baseline_id}")
        doc_id = uuid.uuid4().hex
        storage_url = self.doc_store.put(baseline_id, doc_id, filename, data)
        doc = db.Document(
            id=doc_id,
            baseline_id=baseline_id,
            filename=Path(filename).name or "document",
            storage_url=storage_url,
            content_hash=content_hash(data),
        )
        self.s.add(doc)
        self.s.flush()
        return doc

    # -- merge jobs ----------------------------------------------------
    def create_merge_job(self, baseline_id: str, document_id: Optional[str]) -> db.MergeJob:
        if self.get_project(baseline_id) is None:
            raise NotFoundError(f"项目不存在：{baseline_id}")
        if document_id is not None:
            # Tenant isolation: the document must belong to this project, or an
            # editor of A could merge (and thereby read) project B's document.
            doc = self.s.get(db.Document, document_id)
            if doc is None or doc.baseline_id != baseline_id:
                raise NotFoundError(f"文档不存在：{document_id}")
        job = db.MergeJob(
            id=uuid.uuid4().hex,
            baseline_id=baseline_id,
            document_id=document_id,
            status="queued",
        )
        self.s.add(job)
        self.s.flush()
        return job

    def get_merge_job(self, job_id: str) -> db.MergeJob:
        job = self.s.get(db.MergeJob, job_id)
        if job is None:
            raise NotFoundError(f"任务不存在：{job_id}")
        return job

    # -- shared app config ---------------------------------------------
    def get_app_config(self) -> dict:
        row = self.s.get(db.AppConfig, 1)
        return dict(row.data) if row and row.data else {}

    def set_app_config(self, data: dict, updated_by: Optional[str] = None) -> dict:
        row = self.s.get(db.AppConfig, 1)
        if row is None:
            row = db.AppConfig(id=1, data=data, updated_by=updated_by)
            self.s.add(row)
        else:
            row.data = data
            row.updated_by = updated_by
        self.s.flush()
        return dict(row.data)
