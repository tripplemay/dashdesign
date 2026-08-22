"""Authentication persistence checks, including enforced foreign keys."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from cloud.server import auth, db


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
