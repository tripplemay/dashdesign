"""Integration tests for the FastAPI baseline cloud API (in-memory SQLite)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from cloud.server import auth, db  # noqa: E402
from cloud.server.app import create_app  # noqa: E402
from cloud.server.config import Settings  # noqa: E402
from tests.baseline_fixtures import base_baseline, dirty  # noqa: E402

_ADMIN = "admin-token"
_ADMIN_PW = "admin-pass-123"
_DOC_TEXT = "本课程面向青少年的专业创作课程，注重动手实践与作品产出。"
_QUOTE = "面向青少年的专业创作课程"


def _fake_chat(messages=None, response_format=None):
    return json.dumps(
        {
            "source_context_hint": "mixed_docs",
            "evidence": [{"id": "ev_1", "section": "第 1 页", "quote": _QUOTE}],
            "candidates": [
                {"target": "consumer_baseline.core_messages", "text": _QUOTE,
                 "confidence": 0.8, "evidence": ["ev_1"]}
            ],
        }
    )


@pytest.fixture()
def app_ctx(tmp_path):
    engine = create_engine(
        "sqlite://", future=True, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    settings = Settings(
        db_url="sqlite://", doc_store="local", doc_root=tmp_path / "docs",
        oss_bucket="", oss_endpoint="", oss_prefix="p/", admin_token=_ADMIN,
        admin_password=_ADMIN_PW, seed_demo=False,
    )
    app = create_app(settings=settings, engine=engine, chat_factory=lambda model: _fake_chat)
    return app


@pytest.fixture()
def client(app_ctx):
    return TestClient(app_ctx)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _mint(app, user_id, token, is_admin=False, project=None, role=None, global_role=None):
    with app.state.session_factory() as s:
        auth.ensure_user_with_token(s, user_id, user_id, token, is_admin, global_role=global_role)
        if project and role:
            auth.add_member(s, project, user_id, role)
        s.commit()


def _create_project(client, baseline_id="proj_a"):
    return client.post("/projects", json=base_baseline(baseline_id), headers=_h(_ADMIN))


class TestAuth:
    def test_missing_token_401(self, client):
        assert client.get("/projects").status_code == 401

    def test_bad_token_401(self, client):
        assert client.get("/projects", headers=_h("nope")).status_code == 401

    def test_healthz_open(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}


class TestProjectLifecycle:
    def test_create_list_get(self, client):
        resp = _create_project(client)
        assert resp.status_code == 201
        assert resp.json()["baseline_id"] == "proj_a"
        listing = client.get("/projects", headers=_h(_ADMIN)).json()
        assert [p["baseline_id"] for p in listing] == ["proj_a"]
        got = client.get("/projects/proj_a", headers=_h(_ADMIN)).json()
        assert got["active_version"] == "2026.07.06.1"

    def test_duplicate_409(self, client):
        _create_project(client)
        resp = _create_project(client)
        assert resp.status_code == 409
        assert resp.json()["code"] == "conflict"

    def test_invalid_422(self, client):
        bad = base_baseline("proj_bad")
        del bad["consumer_baseline"]
        resp = client.post("/projects", json=bad, headers=_h(_ADMIN))
        assert resp.status_code == 422
        assert resp.json()["code"] == "validation_error"

    def test_dirty_422_governance(self, client):
        resp = client.post("/projects", json=dirty(base_baseline("proj_dirty")), headers=_h(_ADMIN))
        assert resp.status_code == 422
        assert resp.json()["code"] == "governance_error"

    def test_get_missing_404(self, client):
        assert client.get("/projects/ghost", headers=_h(_ADMIN)).status_code == 404


class TestVersionsAndConcurrency:
    def test_load_version_has_etag(self, client):
        _create_project(client)
        resp = client.get("/projects/proj_a/versions/2026.07.06.1", headers=_h(_ADMIN))
        assert resp.status_code == 200
        assert resp.headers.get("ETag")

    def test_stale_if_match_conflicts(self, client):
        _create_project(client)
        # Fetch parent, derive a draft, save it (new version).
        draft = base_baseline("proj_a", "2026.07.06.2")
        draft["parent_version"] = "2026.07.06.1"
        first = client.post("/projects/proj_a/drafts", json=draft, headers=_h(_ADMIN))
        assert first.status_code == 201
        etag = first.json()["etag"]
        # Overwrite with correct ETag -> ok.
        draft["project"]["name"] = "改名"
        ok = client.post(
            "/projects/proj_a/drafts", json=draft, headers={**_h(_ADMIN), "If-Match": etag}
        )
        assert ok.status_code == 201
        # Overwrite again with the STALE etag -> 409.
        draft["project"]["name"] = "再改"
        stale = client.post(
            "/projects/proj_a/drafts", json=draft, headers={**_h(_ADMIN), "If-Match": etag}
        )
        assert stale.status_code == 409

    def test_blind_overwrite_without_if_match_409(self, client):
        _create_project(client)
        draft = base_baseline("proj_a", "2026.07.06.2")
        draft["parent_version"] = "2026.07.06.1"
        assert client.post("/projects/proj_a/drafts", json=draft, headers=_h(_ADMIN)).status_code == 201
        # Second save of the same version WITHOUT If-Match must not clobber.
        draft["project"]["name"] = "覆盖"
        resp = client.post("/projects/proj_a/drafts", json=draft, headers=_h(_ADMIN))
        assert resp.status_code == 409
        assert resp.json()["code"] == "conflict"

    def test_integrity_error_maps_to_409(self, client, monkeypatch):
        from sqlalchemy.exc import IntegrityError

        from cloud.server import store as store_mod

        def _boom(self, *a, **k):
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        monkeypatch.setattr(store_mod.SqlBaselineStore, "create_project", _boom)
        resp = client.post("/projects", json=base_baseline("race_proj"), headers=_h(_ADMIN))
        assert resp.status_code == 409
        assert resp.json()["code"] == "conflict"

    def test_publish_and_set_active(self, client):
        _create_project(client)
        draft = base_baseline("proj_a", "2026.07.06.2")
        draft["parent_version"] = "2026.07.06.1"
        client.post("/projects/proj_a/drafts", json=draft, headers=_h(_ADMIN))
        pub = client.post("/projects/proj_a/versions/2026.07.06.2/publish", headers=_h(_ADMIN))
        assert pub.status_code == 200
        assert pub.json()["status"] == "published"
        active = client.put(
            "/projects/proj_a/active", json={"version": "2026.07.06.1"}, headers=_h(_ADMIN)
        )
        assert active.status_code == 204


class TestRoles:
    def test_reviewer_can_read_not_write(self, client, app_ctx):
        _create_project(client)
        _mint(app_ctx, "rev", "rev-token", project="proj_a", role="reviewer")
        assert client.get("/projects/proj_a", headers=_h("rev-token")).status_code == 200
        draft = base_baseline("proj_a", "2026.07.06.2")
        resp = client.post("/projects/proj_a/drafts", json=draft, headers=_h("rev-token"))
        assert resp.status_code == 403

    def test_editor_can_write(self, client, app_ctx):
        _create_project(client)
        _mint(app_ctx, "ed", "ed-token", project="proj_a", role="editor")
        draft = base_baseline("proj_a", "2026.07.06.2")
        draft["parent_version"] = "2026.07.06.1"
        resp = client.post("/projects/proj_a/drafts", json=draft, headers=_h("ed-token"))
        assert resp.status_code == 201

    def test_non_member_cannot_read(self, client, app_ctx):
        _create_project(client)
        _mint(app_ctx, "outsider", "out-token")
        assert client.get("/projects/proj_a", headers=_h("out-token")).status_code == 403
        # And it does not appear in their project list.
        assert client.get("/projects", headers=_h("out-token")).json() == []


class TestAppConfigAndAdmin:
    def test_default_config_empty(self, client):
        cfg = client.get("/app-config", headers=_h(_ADMIN)).json()
        assert cfg["image_api_key"] == ""
        assert cfg["baseline_model"] == "gpt-4o"

    def test_config_requires_auth(self, client):
        assert client.get("/app-config").status_code == 401

    def test_admin_password_gates_put(self, client):
        body = {"image_api_base_url": "https://gw/v1", "image_api_key": "sk-xyz", "baseline_model": "gpt-5.5"}
        # Wrong password -> 403.
        assert client.put("/app-config", json=body, headers={"X-Admin-Password": "nope"}).status_code == 403
        # Correct password -> saved.
        ok = client.put("/app-config", json=body, headers={"X-Admin-Password": _ADMIN_PW})
        assert ok.status_code == 200 and ok.json()["image_api_key"] == "sk-xyz"
        # Now every client (shared token) reads it, no local setup.
        got = client.get("/app-config", headers=_h(_ADMIN)).json()
        assert got["image_api_base_url"] == "https://gw/v1"
        assert got["image_api_key"] == "sk-xyz"
        assert got["baseline_model"] == "gpt-5.5"

    def test_admin_verify(self, client):
        assert client.post("/admin/verify", headers={"X-Admin-Password": _ADMIN_PW}).json() == {"ok": True}
        assert client.post("/admin/verify", headers={"X-Admin-Password": "bad"}).status_code == 403

    def test_change_password(self, client):
        # Wrong current -> 403.
        bad = client.post("/admin/change-password", json={"current_password": "nope", "new_password": "newpass1"})
        assert bad.status_code == 403
        # Too short -> rejected.
        short = client.post("/admin/change-password", json={"current_password": _ADMIN_PW, "new_password": "12"})
        assert short.status_code == 400
        # Correct change.
        ok = client.post("/admin/change-password", json={"current_password": _ADMIN_PW, "new_password": "brand-new-pw"})
        assert ok.json() == {"ok": True}
        # Old env password no longer works; the new one does.
        assert client.post("/admin/verify", headers={"X-Admin-Password": _ADMIN_PW}).status_code == 403
        assert client.post("/admin/verify", headers={"X-Admin-Password": "brand-new-pw"}).json() == {"ok": True}
        # The new password now gates config edits too.
        body = {"image_api_base_url": "", "image_api_key": "k", "baseline_model": "gpt-4o"}
        assert client.put("/app-config", json=body, headers={"X-Admin-Password": "brand-new-pw"}).status_code == 200


class TestGlobalRole:
    def test_global_editor_sees_and_writes_all_projects(self, client, app_ctx):
        _create_project(client, "proj_a")
        _create_project(client, "proj_b")
        _mint(app_ctx, "shared", "shared-token", global_role="editor")
        # A shared global-editor identity sees every project without membership...
        seen = {p["baseline_id"] for p in client.get("/projects", headers=_h("shared-token")).json()}
        assert {"proj_a", "proj_b"} <= seen
        # ...and can write drafts to any of them.
        draft = base_baseline("proj_a", "2026.07.06.2")
        draft["parent_version"] = "2026.07.06.1"
        assert client.post("/projects/proj_a/drafts", json=draft, headers=_h("shared-token")).status_code == 201


class TestDocumentsAndMerge:
    def test_upload_document(self, client):
        _create_project(client)
        resp = client.post(
            "/projects/proj_a/documents",
            files={"file": ("note.txt", b"hello", "text/plain")},
            headers=_h(_ADMIN),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["document_id"] and body["url"].startswith("file://")

    def test_merge_job_inline_text(self, client):
        _create_project(client)
        resp = client.post(
            "/projects/proj_a/merge-jobs",
            json={"text": _DOC_TEXT, "filename": "brief.txt"},
            headers=_h(_ADMIN),
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        status = client.get(f"/merge-jobs/{job_id}", headers=_h(_ADMIN)).json()
        assert status["status"] == "done"
        targets = [c["target"] for c in status["report"]["changes"]]
        assert "consumer_baseline.core_messages" in targets

    def test_merge_job_error_recorded(self, client):
        _create_project(client)
        # Neither text nor document_id -> job persists with error, not a 500.
        resp = client.post("/projects/proj_a/merge-jobs", json={}, headers=_h(_ADMIN))
        assert resp.status_code == 200
        status = client.get(f"/merge-jobs/{resp.json()['job_id']}", headers=_h(_ADMIN)).json()
        assert status["status"] == "error"
        assert status["error"]

    def test_merge_job_foreign_document_404(self, client):
        # IDOR guard: an editor of A cannot merge (and thus read) B's document.
        _create_project(client, "proj_a")
        _create_project(client, "proj_b")
        up = client.post(
            "/projects/proj_b/documents",
            files={"file": ("secret.txt", b"project B private text", "text/plain")},
            headers=_h(_ADMIN),
        )
        doc_id = up.json()["document_id"]
        resp = client.post(
            "/projects/proj_a/merge-jobs", json={"document_id": doc_id}, headers=_h(_ADMIN)
        )
        assert resp.status_code == 404
