"""Unit tests for the Qt-free baseline domain layer."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app_runtime import baseline_path
from baseline import governance, versioning
from baseline.errors import BaselineError, GovernanceError, ValidationError
from baseline.schema import validation_errors
from baseline.store import BaselineRepository


@pytest.fixture(scope="module")
def bundled_baseline() -> dict:
    return json.loads(baseline_path().read_text(encoding="utf-8"))


class TestSchema:
    def test_bundled_baseline_is_valid(self, bundled_baseline: dict) -> None:
        assert validation_errors(bundled_baseline) == []

    def test_missing_required_field_flagged(self, bundled_baseline: dict) -> None:
        broken = copy.deepcopy(bundled_baseline)
        del broken["consumer_baseline"]
        errors = validation_errors(broken)
        assert any("consumer_baseline" in e for e in errors)

    def test_bad_version_pattern_flagged(self, bundled_baseline: dict) -> None:
        broken = copy.deepcopy(bundled_baseline)
        broken["version"] = "v1"
        assert any("version" in e for e in validation_errors(broken))


class TestVersioning:
    def test_next_version_increments_per_day(self) -> None:
        assert versioning.next_version("2026.07.08", []) == "2026.07.08.1"
        assert versioning.next_version("2026.07.08", ["2026.07.08.1"]) == "2026.07.08.2"
        assert versioning.next_version("2026.07.08", ["2026.07.06.1"]) == "2026.07.08.1"

    def test_sort_versions(self) -> None:
        assert versioning.sort_versions(["2026.07.08.2", "2026.07.06.1", "2026.07.08.1"]) == [
            "2026.07.06.1",
            "2026.07.08.1",
            "2026.07.08.2",
        ]

    def test_new_draft_from_links_parent_and_does_not_mutate(self, bundled_baseline: dict) -> None:
        parent = copy.deepcopy(bundled_baseline)
        parent["status"] = "published"
        draft = versioning.new_draft_from(parent, "2026.07.09", [parent["version"]])
        assert draft["status"] == "draft"
        assert draft["parent_version"] == parent["version"]
        assert draft["version"] == "2026.07.09.1"
        assert parent["status"] == "published"  # 未被修改


class TestGovernance:
    def test_clean_baseline_no_issues(self, bundled_baseline: dict) -> None:
        assert governance.governance_issues(bundled_baseline) == []

    def test_blocked_keyword_in_consumer_layer_detected(self, bundled_baseline: dict) -> None:
        dirty = copy.deepcopy(bundled_baseline)
        blocked = dirty["consumer_baseline"]["blocked_keywords"][0]
        dirty["consumer_baseline"]["core_messages"].append(
            {"text": f"欢迎{blocked}加入", "evidence": []}
        )
        hits = governance.blocked_keyword_hits(dirty)
        assert blocked in hits
        assert governance.governance_issues(dirty)


class TestRepository:
    def test_create_list_and_active(self, tmp_path: Path, bundled_baseline: dict) -> None:
        repo = BaselineRepository(tmp_path)
        assert repo.list_projects() == []
        info = repo.create_project(copy.deepcopy(bundled_baseline))
        assert info.baseline_id == bundled_baseline["baseline_id"]
        assert info.active_version == bundled_baseline["version"]
        assert repo.list_versions(info.baseline_id) == [bundled_baseline["version"]]
        assert repo.active_baseline_path(info.baseline_id).exists()

    def test_duplicate_project_rejected(self, tmp_path: Path, bundled_baseline: dict) -> None:
        repo = BaselineRepository(tmp_path)
        repo.create_project(copy.deepcopy(bundled_baseline))
        with pytest.raises(BaselineError, match="已存在"):
            repo.create_project(copy.deepcopy(bundled_baseline))

    def test_invalid_baseline_rejected_on_create(self, tmp_path: Path, bundled_baseline: dict) -> None:
        repo = BaselineRepository(tmp_path)
        broken = copy.deepcopy(bundled_baseline)
        del broken["governance"]
        with pytest.raises(ValidationError):
            repo.create_project(broken)

    def test_draft_publish_flow(self, tmp_path: Path, bundled_baseline: dict) -> None:
        repo = BaselineRepository(tmp_path)
        base = copy.deepcopy(bundled_baseline)
        bid = base["baseline_id"]
        repo.create_project(base)
        # 建草稿
        draft = repo.new_draft(bid, base["version"])
        assert draft["status"] == "draft"
        assert draft["parent_version"] == base["version"]
        new_version = repo.save_draft(draft)
        assert new_version in repo.list_versions(bid)
        # 发布 → active 指向新版本、状态 published
        published = repo.publish(bid, new_version)
        assert published["status"] == "published"
        assert repo.active_version(bid) == new_version

    def test_cannot_save_non_draft(self, tmp_path: Path, bundled_baseline: dict) -> None:
        repo = BaselineRepository(tmp_path)
        base = copy.deepcopy(bundled_baseline)
        repo.create_project(base)
        published = copy.deepcopy(base)
        published["status"] = "published"
        with pytest.raises(BaselineError, match="draft"):
            repo.save_draft(published)

    def test_publish_blocked_by_governance(self, tmp_path: Path, bundled_baseline: dict) -> None:
        repo = BaselineRepository(tmp_path)
        base = copy.deepcopy(bundled_baseline)
        bid = base["baseline_id"]
        repo.create_project(base)
        draft = repo.new_draft(bid, base["version"])
        blocked = draft["consumer_baseline"]["blocked_keywords"][0]
        draft["consumer_baseline"]["core_messages"].append(
            {"text": f"限时{blocked}", "evidence": []}
        )
        version = repo.save_draft(draft)
        with pytest.raises(GovernanceError):
            repo.publish(bid, version)

    def test_add_document(self, tmp_path: Path, bundled_baseline: dict) -> None:
        repo = BaselineRepository(tmp_path)
        base = copy.deepcopy(bundled_baseline)
        repo.create_project(base)
        src = tmp_path / "intro.txt"
        src.write_text("项目介绍", encoding="utf-8")
        stored = repo.add_document(base["baseline_id"], src)
        assert stored.exists()
        assert stored.read_text(encoding="utf-8") == "项目介绍"
