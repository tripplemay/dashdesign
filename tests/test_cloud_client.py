"""End-to-end tests: HttpBaselineRepository (requests) against a live server.

Boots the FastAPI app under uvicorn in a background thread and drives the desktop
client repository over real HTTP, exercising the same BaselineRepository surface
the GUI uses — including path materialization and multi-client ETag concurrency.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")
pytest.importorskip("sqlalchemy")

import requests  # noqa: E402
import uvicorn  # noqa: E402

from baseline.errors import BaselineError, GovernanceError  # noqa: E402
from cloud.client import HttpBaselineRepository  # noqa: E402
from cloud.server.app import create_app  # noqa: E402
from cloud.server.config import Settings  # noqa: E402
from tests.baseline_fixtures import base_baseline, dirty  # noqa: E402

_ADMIN = "admin-token"


def _fake_chat(messages=None, response_format=None):
    return json.dumps({"source_context_hint": "manual", "evidence": [], "candidates": []})


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # runs off the main thread
        pass


@pytest.fixture()
def live_base(tmp_path):
    port = _free_port()
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'cloud.db'}", doc_store="local",
        doc_root=tmp_path / "docs", oss_bucket="", oss_endpoint="", oss_prefix="p/",
        admin_token=_ADMIN, admin_password="pw", seed_demo=False,
    )
    app = create_app(settings=settings, chat_factory=lambda model: _fake_chat)
    server = _Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if requests.get(base + "/healthz", timeout=0.5).status_code == 200:
                break
        except requests.RequestException:
            time.sleep(0.05)
    else:
        raise RuntimeError("server did not start")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def repo(live_base, tmp_path):
    return HttpBaselineRepository(live_base, _ADMIN, tmp_path / "cache")


class TestClientLifecycle:
    def test_full_project_flow(self, repo):
        info = repo.create_project(base_baseline("cloud_proj"))
        assert info.baseline_id == "cloud_proj"
        assert [p.baseline_id for p in repo.list_projects()] == ["cloud_proj"]
        assert repo.active_version("cloud_proj") == "2026.07.06.1"

        data = repo.load_version("cloud_proj", "2026.07.06.1")
        assert data["baseline_id"] == "cloud_proj"

        draft = repo.new_draft("cloud_proj", "2026.07.06.1")
        assert draft["status"] == "draft"
        new_version = repo.save_draft(draft)
        assert new_version in repo.list_versions("cloud_proj")

        published = repo.publish("cloud_proj", new_version)
        assert published["status"] == "published"
        assert repo.active_version("cloud_proj") == new_version

    def test_active_baseline_path_materializes_file(self, repo):
        repo.create_project(base_baseline("pathproj"))
        path = repo.active_baseline_path("pathproj")
        assert path is not None and path.exists()
        cached = json.loads(path.read_text(encoding="utf-8"))
        assert cached["baseline_id"] == "pathproj"

    def test_version_path_fetches_on_demand(self, repo):
        repo.create_project(base_baseline("vp_proj"))
        path = repo.version_path("vp_proj", "2026.07.06.1")
        assert path.exists()

    def test_get_missing_project_returns_none(self, repo):
        assert repo.get_project("does_not_exist") is None

    def test_add_document(self, repo, tmp_path):
        repo.create_project(base_baseline("docproj"))
        src = tmp_path / "brief.txt"
        src.write_text("some source text", encoding="utf-8")
        # Returns without error (server stored it); original stays local.
        assert repo.add_document("docproj", src) == src


    def test_merge_job_background_completes_on_real_server(self, live_base):
        # Verifies the background task runs to completion under real uvicorn
        # (response returns first, extraction commits done/error afterwards).
        h = {"Authorization": f"Bearer {_ADMIN}"}
        requests.post(f"{live_base}/projects", json=base_baseline("bgjob"), headers=h)
        created = requests.post(
            f"{live_base}/projects/bgjob/merge-jobs",
            json={"text": "面向青少年的专业创作课程", "filename": "brief.txt"},
            headers=h,
        )
        job_id = created.json()["job_id"]
        status = None
        for _ in range(100):
            status = requests.get(f"{live_base}/merge-jobs/{job_id}", headers=h).json()
            if status["status"] in ("done", "error"):
                break
            time.sleep(0.05)
        assert status["status"] == "done", status
        assert "changes" in status["report"]

    def test_version_path_revalidates_across_clients(self, live_base, tmp_path):
        editor_a = HttpBaselineRepository(live_base, _ADMIN, tmp_path / "a")
        editor_b = HttpBaselineRepository(live_base, _ADMIN, tmp_path / "b")
        editor_a.create_project(base_baseline("revalidate"))
        draft = editor_a.new_draft("revalidate", "2026.07.06.1")
        version = editor_a.save_draft(draft)  # A holds the ETag

        # B caches the draft, then A overwrites it server-side.
        editor_b.version_path("revalidate", version)
        draft["project"]["name"] = "A 最新改动"
        editor_a.save_draft(draft)

        # B's version_path must revalidate and reflect A's change, not stale cache.
        path = editor_b.version_path("revalidate", version)
        assert json.loads(path.read_text(encoding="utf-8"))["project"]["name"] == "A 最新改动"


class TestClientOffline:
    """These need no server — the client short-circuits or falls back to cache."""

    def test_empty_baseline_id_is_none(self, tmp_path):
        repo = HttpBaselineRepository("http://127.0.0.1:1", _ADMIN, tmp_path / "cache")
        assert repo.get_project("") is None
        assert repo.get_project("   ") is None
        assert repo.active_version("") is None
        assert repo.active_baseline_path("") is None

    def test_offline_active_path_prefers_marker_then_numeric_newest(self, tmp_path):
        cache = tmp_path / "cache"
        repo = HttpBaselineRepository("http://127.0.0.1:1", _ADMIN, cache)
        vdir = cache / "proj" / "versions"
        vdir.mkdir(parents=True)
        for ver in ("2026.07.06.1", "2026.07.06.2", "2026.07.06.10"):
            (vdir / f"{ver}.json").write_text(
                json.dumps({"baseline_id": "proj", "version": ver}), encoding="utf-8"
            )
        # With an active marker -> returns exactly the active (pinned) version.
        (cache / "proj" / "active.json").write_text(json.dumps({"active": "2026.07.06.2"}), encoding="utf-8")
        assert repo.active_baseline_path("proj").stem == "2026.07.06.2"
        # Without a marker -> numeric-newest (.10 beats .2; NOT lexical order).
        (cache / "proj" / "active.json").unlink()
        assert repo.active_baseline_path("proj").stem == "2026.07.06.10"


class TestClientErrors:
    def test_governance_error_maps_to_domain(self, repo):
        with pytest.raises(GovernanceError):
            repo.create_project(dirty(base_baseline("dirtyproj")))

    def test_stale_etag_conflict_across_clients(self, live_base, tmp_path):
        editor_a = HttpBaselineRepository(live_base, _ADMIN, tmp_path / "a")
        editor_b = HttpBaselineRepository(live_base, _ADMIN, tmp_path / "b")
        editor_a.create_project(base_baseline("shared"))

        # Both editors derive + save the same draft version; A saves first.
        draft = editor_a.new_draft("shared", "2026.07.06.1")
        version = editor_a.save_draft(draft)  # A now holds the fresh ETag

        # B loads that draft (gets its ETag), then A overwrites it (bumps ETag).
        b_view = editor_b.load_version("shared", version)
        b_view["project"]["name"] = "B 的改动"
        draft["project"]["name"] = "A 的改动"
        editor_a.save_draft(draft)  # A overwrites -> server ETag changes

        # B saving with its now-stale ETag must conflict.
        with pytest.raises(BaselineError):
            editor_b.save_draft(b_view)
