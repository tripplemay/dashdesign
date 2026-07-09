"""Unit tests for the server-authoritative SqlBaselineStore (SQLite-backed)."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from baseline.errors import GovernanceError, ValidationError  # noqa: E402
from cloud.server import db  # noqa: E402
from cloud.server.docstore import LocalDocumentStore  # noqa: E402
from cloud.server.store import (  # noqa: E402
    ConflictError,
    NotFoundError,
    SqlBaselineStore,
    canonical_etag,
)
from tests.baseline_fixtures import base_baseline, dirty  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.create_all(engine)
    session = db.make_session_factory(engine)()
    yield SqlBaselineStore(session, LocalDocumentStore(tmp_path / "docs"))
    session.close()


def _create(store, baseline_id="proj_a", version="2026.07.06.1"):
    baseline = base_baseline(baseline_id, version)
    store.create_project(baseline, owner_user_id="u1")
    return baseline


class TestCreateProject:
    def test_creates_version_membership_and_active(self, store):
        _create(store)
        project = store.get_project("proj_a")
        assert project.active_version == "2026.07.06.1"
        assert store.list_versions("proj_a") == ["2026.07.06.1"]
        membership = store.s.get(db.Membership, {"baseline_id": "proj_a", "user_id": "u1"})
        assert membership.role == "admin"

    def test_duplicate_raises_conflict(self, store):
        _create(store)
        with pytest.raises(ConflictError):
            _create(store)

    def test_invalid_baseline_raises_validation(self, store):
        bad = base_baseline("proj_b")
        del bad["consumer_baseline"]
        with pytest.raises(ValidationError):
            store.create_project(bad, owner_user_id="u1")

    def test_dirty_baseline_blocked_by_governance(self, store):
        with pytest.raises(GovernanceError):
            store.create_project(dirty(base_baseline("proj_c")), owner_user_id="u1")

    def test_illegal_baseline_id_rejected(self, store):
        with pytest.raises(Exception):
            store.create_project(base_baseline("A B!"), owner_user_id="u1")


class TestVersionsAndEtag:
    def test_load_version_returns_stable_etag(self, store):
        baseline = _create(store)
        data, etag = store.load_version("proj_a", "2026.07.06.1")
        assert data["baseline_id"] == "proj_a"
        assert etag == canonical_etag(baseline)

    def test_load_missing_version_raises_not_found(self, store):
        _create(store)
        with pytest.raises(NotFoundError):
            store.load_version("proj_a", "2099.01.01.1")


class TestDrafts:
    def test_new_draft_then_save_appends_version(self, store):
        _create(store)
        draft = store.new_draft("proj_a", "2026.07.06.1")
        assert draft["status"] == "draft"
        assert draft["parent_version"] == "2026.07.06.1"
        version, _ = store.save_draft(draft)
        assert version in store.list_versions("proj_a")
        assert len(store.list_versions("proj_a")) == 2

    def test_overwrite_draft_requires_matching_etag(self, store):
        _create(store)
        draft = store.new_draft("proj_a", "2026.07.06.1")
        version, etag = store.save_draft(draft)
        # Correct ETag succeeds.
        draft["project"]["name"] = "改名一次"
        _, etag2 = store.save_draft(draft, if_match=etag)
        # Stale ETag now conflicts.
        draft["project"]["name"] = "改名两次"
        with pytest.raises(ConflictError):
            store.save_draft(draft, if_match=etag)
        # Fresh ETag succeeds again.
        store.save_draft(draft, if_match=etag2)

    def test_cannot_overwrite_published_version(self, store):
        _create(store)
        store.publish("proj_a", "2026.07.06.1")
        published = base_baseline("proj_a", "2026.07.06.1")  # status draft locally
        with pytest.raises(ConflictError):
            store.save_draft(published)

    def test_blind_overwrite_without_etag_conflicts(self, store):
        # A second operator who created the same version the same day must NOT
        # silently clobber the first draft: missing If-Match is a conflict.
        _create(store)
        draft = store.new_draft("proj_a", "2026.07.06.1")
        store.save_draft(draft)  # first INSERT succeeds (no existing row)
        draft["project"]["name"] = "并发覆盖"
        with pytest.raises(ConflictError):
            store.save_draft(draft)  # overwrite without If-Match -> 409


class TestPublish:
    def test_publish_flips_status_and_sets_active(self, store):
        _create(store)
        draft = store.new_draft("proj_a", "2026.07.06.1")
        version, _ = store.save_draft(draft)
        published = store.publish("proj_a", version)
        assert published["status"] == "published"
        assert store.active_version("proj_a") == version

    def test_publish_is_idempotent(self, store):
        _create(store)
        first = store.publish("proj_a", "2026.07.06.1")
        second = store.publish("proj_a", "2026.07.06.1")
        assert first["status"] == second["status"] == "published"

    def test_publish_blocked_by_governance(self, store):
        _create(store)
        draft = dirty(store.new_draft("proj_a", "2026.07.06.1"))
        draft["status"] = "draft"
        # Draft persists (schema-valid) but publish must fail the governance gate.
        version, _ = store.save_draft(draft)
        with pytest.raises(GovernanceError):
            store.publish("proj_a", version)


class TestSetActive:
    def test_set_active_governance_gate(self, store):
        _create(store)
        draft = dirty(store.new_draft("proj_a", "2026.07.06.1"))
        draft["status"] = "draft"
        version, _ = store.save_draft(draft)
        with pytest.raises(GovernanceError):
            store.set_active_version("proj_a", version)


class TestDocumentsAndJobs:
    def test_add_document_stores_and_hashes(self, store):
        _create(store)
        doc = store.add_document("proj_a", "note.txt", b"hello world")
        assert doc.filename == "note.txt"
        assert len(doc.content_hash) == 64
        assert doc.storage_url.startswith("file://")

    def test_merge_job_lifecycle(self, store):
        _create(store)
        job = store.create_merge_job("proj_a", None)
        assert job.status == "queued"
        assert store.get_merge_job(job.id).id == job.id
        with pytest.raises(NotFoundError):
            store.get_merge_job("nope")

    def test_merge_job_rejects_foreign_document(self, store):
        # A document uploaded under project B cannot be merged into project A.
        _create(store, "proj_a")
        _create(store, "proj_b")
        doc_b = store.add_document("proj_b", "b.txt", b"secret B content")
        with pytest.raises(NotFoundError):
            store.create_merge_job("proj_a", doc_b.id)
