"""Unit tests for baseline_service.load_overview — the de-duplicated, few-request
aggregate that replaced the page's repeated /projects + per-version downloads."""

from __future__ import annotations

import collections

import pytest

from baseline.store import ProjectInfo, VersionSummary
from ui import baseline_service as bs


class _FakeRepo:
    """Counts calls so tests can assert de-duplication and no per-version load."""

    def __init__(self, projects, summaries_by_pid, payloads) -> None:
        self._projects = projects
        self._summaries = summaries_by_pid
        self._payloads = payloads
        self.calls: collections.Counter = collections.Counter()

    def list_projects(self):
        self.calls["list_projects"] += 1
        return self._projects

    def list_version_summaries(self, pid):
        self.calls["summaries"] += 1
        return self._summaries.get(pid, [])

    def load_version(self, pid, version):
        self.calls["load_version"] += 1
        return self._payloads[(pid, version)]


def _projects():
    # active_version is intentionally NOT the newest, so tests catch any regression
    # in "default selects newest" vs "default selects active".
    return [
        ProjectInfo("proj_a", "A", active_version="2026.01.01.1", versions=["2026.01.01.1", "2026.01.01.2"]),
        ProjectInfo("proj_b", "B", active_version=None, versions=[]),
    ]


def _fake():
    return _FakeRepo(
        _projects(),
        {
            # Ascending, exactly like both real repos (versioning.sort_versions / server).
            "proj_a": [
                VersionSummary("2026.01.01.1", "published"),
                VersionSummary("2026.01.01.2", "draft"),
            ]
        },
        {
            ("proj_a", "2026.01.01.2"): {"version": "2026.01.01.2", "status": "draft"},
            ("proj_a", "2026.01.01.1"): {"version": "2026.01.01.1", "status": "published"},
        },
    )


class TestResolveActivePid:
    def test_selected_wins_when_valid(self) -> None:
        assert bs._resolve_active_pid(["a", "b"], "b", "a") == "b"

    def test_stored_used_when_no_valid_selection(self) -> None:
        assert bs._resolve_active_pid(["a", "b"], None, "b") == "b"

    def test_falls_back_to_first_when_both_invalid(self) -> None:
        assert bs._resolve_active_pid(["a", "b"], "zzz", "zzz") == "a"

    def test_none_when_no_projects(self) -> None:
        assert bs._resolve_active_pid([], None, "") is None


class TestLoadOverview:
    def test_dedups_and_avoids_per_version_load(self, monkeypatch) -> None:
        fake = _fake()
        monkeypatch.setattr(bs, "repository", lambda: fake)
        monkeypatch.setattr(bs, "_stored_active_project", lambda: "")
        ov = bs.load_overview()
        assert ov.active_project_id == "proj_a"  # first project
        assert ov.active_version == "2026.01.01.1"  # from ProjectInfo (need not be newest)
        assert [v.version for v in ov.versions] == ["2026.01.01.1", "2026.01.01.2"]  # ascending; UI reverses
        assert ov.selected_version == "2026.01.01.2"  # default = newest, matches old behavior
        assert ov.selected_payload["version"] == "2026.01.01.2"
        # The whole point: one /projects, one /versions, one version download.
        assert fake.calls["list_projects"] == 1
        assert fake.calls["summaries"] == 1
        assert fake.calls["load_version"] == 1

    def test_stored_active_project_selected(self, monkeypatch) -> None:
        fake = _fake()
        monkeypatch.setattr(bs, "repository", lambda: fake)
        monkeypatch.setattr(bs, "_stored_active_project", lambda: "proj_b")
        ov = bs.load_overview()
        assert ov.active_project_id == "proj_b"
        assert ov.versions == []
        assert ov.selected_version is None
        assert ov.selected_payload is None
        assert fake.calls["load_version"] == 0  # no versions -> nothing to download

    def test_explicit_selection_overrides(self, monkeypatch) -> None:
        fake = _fake()
        monkeypatch.setattr(bs, "repository", lambda: fake)
        monkeypatch.setattr(bs, "_stored_active_project", lambda: "")
        ov = bs.load_overview(selected_project="proj_a", selected_version="2026.01.01.1")
        assert ov.selected_version == "2026.01.01.1"
        assert ov.selected_payload["version"] == "2026.01.01.1"

    def test_no_projects(self, monkeypatch) -> None:
        fake = _FakeRepo([], {}, {})
        monkeypatch.setattr(bs, "repository", lambda: fake)
        monkeypatch.setattr(bs, "_stored_active_project", lambda: "")
        ov = bs.load_overview()
        assert ov.active_project_id is None
        assert ov.projects == []
        assert ov.versions == []
        assert ov.selected_payload is None
