"""Authentication persistence checks, including enforced foreign keys."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from cloud.server import auth, db
from cloud.server.app import create_app
from cloud.server.config import Settings
from tests.baseline_fixtures import base_baseline


def test_bootstrap_token_inserts_user_before_token_with_foreign_keys():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    db.create_all(engine)
    sessions = db.make_session_factory(engine)
    with sessions() as session:
        principal = auth.ensure_user_with_token(session, "admin", "Admin", "token", True)
        session.commit()

    assert principal.is_admin is True
    with sessions() as session:
        assert auth.principal_for_token(session, "token").user_id == "admin"


def test_create_project_inserts_parent_before_children_with_foreign_keys(tmp_path):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    settings = Settings(
        db_url="sqlite://",
        doc_store="local",
        doc_root=tmp_path / "docs",
        oss_bucket="",
        oss_endpoint="",
        oss_prefix="p/",
        admin_token="admin-token",
        admin_password="admin-password",
        seed_demo=False,
    )
    app = create_app(settings=settings, engine=engine)
    with TestClient(app) as client:
        response = client.post(
            "/projects",
            json=base_baseline("foreign_key_project"),
            headers={"Authorization": "Bearer admin-token"},
        )

    assert response.status_code == 201
    assert response.json()["baseline_id"] == "foreign_key_project"
