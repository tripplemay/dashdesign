"""Upgrade recovery tests for filesystem and legacy cloud-cache baselines."""

from __future__ import annotations

import copy
import json

from baseline.migration import migrate_legacy_projects
from baseline.store import BaselineRepository, ProjectInfo
from tests.baseline_fixtures import base_baseline


class _CloudRepo:
    def __init__(self) -> None:
        self.data = {}
        self.active = {}
        self.import_calls = []

    def project_infos(self):
        return [
            ProjectInfo(
                baseline_id,
                versions[active]["project"]["name"],
                active,
                sorted(versions),
            )
            for baseline_id, versions in self.data.items()
            for active in [self.active[baseline_id]]
        ]

    def seed(self, versions, active_version):
        baseline_id = versions[0]["baseline_id"]
        self.data[baseline_id] = {
            item["version"]: copy.deepcopy(item) for item in versions
        }
        self.active[baseline_id] = active_version

    def import_project(self, versions, active_version):
        self.import_calls.append((copy.deepcopy(versions), active_version))
        self.seed(versions, active_version)
        baseline_id = versions[0]["baseline_id"]
        return next(item for item in self.project_infos() if item.baseline_id == baseline_id)

    def load_version(self, baseline_id, version):
        return copy.deepcopy(self.data[baseline_id][version])


def _history(baseline_id):
    published = base_baseline(baseline_id, "2026.07.06.1")
    published["status"] = "published"
    draft = base_baseline(baseline_id, "2026.07.06.2")
    draft["parent_version"] = published["version"]
    return [published, draft]


def _write_local_project(root, baseline_id="local_project"):
    repo = BaselineRepository(root)
    published, draft = _history(baseline_id)
    initial = copy.deepcopy(published)
    initial["status"] = "draft"
    repo.create_project(initial)
    repo.publish(baseline_id, initial["version"])
    repo.save_draft(draft)
    return repo


def _write_cache(root, versions, active_version):
    baseline_id = versions[0]["baseline_id"]
    project = root / baseline_id
    version_dir = project / "versions"
    version_dir.mkdir(parents=True)
    for payload in versions:
        (version_dir / f"{payload['version']}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    (project / "active.json").write_text(
        json.dumps({"active": active_version}), encoding="utf-8"
    )


def test_imports_all_local_versions_without_modifying_source(tmp_path):
    local_root = tmp_path / "baselines"
    local = _write_local_project(local_root)
    before = {
        version: local.version_path("local_project", version).read_bytes()
        for version in local.list_versions("local_project")
    }
    cloud = _CloudRepo()

    summary = migrate_legacy_projects([local_root], [], cloud, [])

    assert summary.migrated == ["local_project"]
    assert cloud.active["local_project"] == "2026.07.06.1"
    assert [item["status"] for item in cloud.import_calls[0][0]] == [
        "published",
        "draft",
    ]
    assert before == {
        version: local.version_path("local_project", version).read_bytes()
        for version in local.list_versions("local_project")
    }


def test_recovers_historical_cloud_cache_only_when_remote_is_empty(tmp_path):
    history = _history("cached_project")
    historical_cache = tmp_path / "Python" / "baseline_cloud_cache"
    _write_cache(historical_cache, history, history[0]["version"])
    cloud = _CloudRepo()

    summary = migrate_legacy_projects(
        [tmp_path / "missing-baselines"],
        [tmp_path / "current-cache", historical_cache],
        cloud,
        [],
    )

    assert summary.migrated == ["cached_project"]
    assert cloud.active["cached_project"] == history[0]["version"]


def test_does_not_resurrect_stale_cache_when_cloud_has_projects(tmp_path):
    cached = _history("cached_project")
    cache_root = tmp_path / "baseline_cloud_cache"
    _write_cache(cache_root, cached, cached[0]["version"])
    remote = _history("remote_project")
    cloud = _CloudRepo()
    cloud.seed(remote, remote[0]["version"])

    summary = migrate_legacy_projects([], [cache_root], cloud, cloud.project_infos())

    assert summary.migrated == []
    assert cloud.import_calls == []
    assert "cached_project" not in cloud.data


def test_never_overwrites_a_different_cloud_project(tmp_path):
    local_root = tmp_path / "baselines"
    _write_local_project(local_root, "same_project")
    remote = _history("same_project")
    remote[0]["project"]["name"] = "云端已修改"
    cloud = _CloudRepo()
    cloud.seed(remote, remote[0]["version"])

    summary = migrate_legacy_projects([local_root], [], cloud, cloud.project_infos())

    assert summary.conflicts == ["same_project"]
    assert summary.migrated == []
    assert cloud.import_calls == []
    assert cloud.data["same_project"]["2026.07.06.1"]["project"]["name"] == "云端已修改"


def test_cloud_superset_is_already_present_even_if_active_advanced(tmp_path):
    local_root = tmp_path / "baselines"
    _write_local_project(local_root, "advanced_project")
    remote = _history("advanced_project")
    latest = base_baseline("advanced_project", "2026.07.06.3")
    latest["parent_version"] = remote[1]["version"]
    remote.append(latest)
    cloud = _CloudRepo()
    cloud.seed(remote, latest["version"])

    summary = migrate_legacy_projects([local_root], [], cloud, cloud.project_infos())

    assert summary.already_present == ["advanced_project"]
    assert summary.conflicts == []
    assert cloud.import_calls == []
