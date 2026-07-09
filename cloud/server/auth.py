"""Token authentication and project membership / role resolution.

Bearer tokens are stored only as SHA-256 hashes (never in plaintext), mirroring
the desktop client's QSettings token pattern on the credential side. Project
access is governed by membership roles: ``reviewer`` (read), ``editor`` (read +
write drafts / documents / merge / publish / set-active), ``admin`` (editor +
create project + manage members). A global-admin user bypasses membership.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from cloud.server import db

_ROLE_RANK = {"reviewer": 1, "editor": 2, "admin": 3}


@dataclass(frozen=True)
class Principal:
    user_id: str
    name: str
    is_admin: bool


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def role_at_least(role: Optional[str], required: str) -> bool:
    return _ROLE_RANK.get(role or "", 0) >= _ROLE_RANK[required]


def principal_for_token(session: Session, token: str) -> Optional[Principal]:
    if not token:
        return None
    row = session.get(db.Token, hash_token(token))
    if row is None:
        return None
    user = session.get(db.User, row.user_id)
    if user is None:
        return None
    return Principal(user_id=user.id, name=user.name, is_admin=user.is_admin)


def role_for(session: Session, baseline_id: str, principal: Principal) -> Optional[str]:
    """Effective role of ``principal`` on ``baseline_id`` (global admin -> admin)."""
    if principal.is_admin:
        return "admin"
    membership = session.get(db.Membership, {"baseline_id": baseline_id, "user_id": principal.user_id})
    return membership.role if membership else None


def ensure_user_with_token(
    session: Session,
    user_id: str,
    name: str,
    token: str,
    is_admin: bool = False,
) -> Principal:
    """Idempotently create a user + bearer token (bootstrap / tests / CLI)."""
    user = session.get(db.User, user_id)
    if user is None:
        user = db.User(id=user_id, name=name, is_admin=is_admin)
        session.add(user)
    else:
        user.name = name
        user.is_admin = is_admin
    token_hash = hash_token(token)
    if session.get(db.Token, token_hash) is None:
        session.add(db.Token(token_hash=token_hash, user_id=user_id))
    session.flush()
    return Principal(user_id=user_id, name=name, is_admin=is_admin)


def add_member(session: Session, baseline_id: str, user_id: str, role: str) -> None:
    if role not in _ROLE_RANK:
        raise ValueError(f"未知角色：{role}")
    existing = session.get(db.Membership, {"baseline_id": baseline_id, "user_id": user_id})
    if existing is None:
        session.add(db.Membership(baseline_id=baseline_id, user_id=user_id, role=role))
    else:
        existing.role = role
    session.flush()
