"""SQLAlchemy engine, session factory, and ORM models for the baseline backend.

Baselines are stored as JSON documents (JSONB on Postgres, JSON on SQLite) in an
append-only ``versions`` table, mirroring the filesystem repository's layout
(``<baseline_id>/versions/<version>.json`` + ``meta.json`` active pointer). The
same model runs on SQLite (dev/tests) and Postgres (prod) from one codebase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    sessionmaker,
)

# JSONB on Postgres (indexable, native), plain JSON elsewhere (SQLite dev/tests).
JsonDoc = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    baseline_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    active_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    org_id: Mapped[str] = mapped_column(String(80), nullable=False, default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    versions: Mapped[list["Version"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Version(Base):
    __tablename__ = "versions"
    __table_args__ = (UniqueConstraint("baseline_id", "version", name="uq_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    baseline_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("projects.baseline_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # draft | published
    parent_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    etag: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JsonDoc, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    project: Mapped[Project] = relationship(back_populates="versions")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    baseline_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("projects.baseline_id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_url: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class MergeJob(Base):
    __tablename__ = "merge_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    baseline_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("projects.baseline_id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # queued|running|done|error
    report: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Token(Base):
    __tablename__ = "tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Membership(Base):
    __tablename__ = "memberships"

    baseline_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("projects.baseline_id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # admin|editor|reviewer


def make_engine(db_url: str):
    kwargs: dict[str, Any] = {"future": True}
    if db_url.startswith("sqlite"):
        # Allow the pooled connection to be shared across FastAPI's threadpool,
        # and wait (not error) up to 30s if another writer holds the lock — the
        # small-team file-SQLite deployment can have brief write contention.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        # In-memory SQLite must keep a single connection or tables vanish.
        if ":memory:" in db_url or db_url in ("sqlite://", "sqlite:///:memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    return create_engine(db_url, **kwargs)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def create_all(engine) -> None:
    Base.metadata.create_all(engine)
