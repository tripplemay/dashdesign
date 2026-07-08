"""Baseline version-string helpers (YYYY.MM.DD.N) and draft derivation."""

from __future__ import annotations

import copy
import re
from typing import List

VERSION_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\.(\d+)$")


def is_version(value: str) -> bool:
    return bool(VERSION_RE.match(value or ""))


def _key(version: str) -> tuple:
    match = VERSION_RE.match(version)
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(g) for g in match.groups())


def sort_versions(versions: List[str]) -> List[str]:
    return sorted((v for v in versions if is_version(v)), key=_key)


def next_version(today: str, existing: List[str]) -> str:
    """Next YYYY.MM.DD.N for ``today`` (YYYY.MM.DD), incrementing N per day."""
    same_day = [v for v in existing if is_version(v) and v.rsplit(".", 1)[0] == today]
    next_n = 1 + max((int(v.rsplit(".", 1)[1]) for v in same_day), default=0)
    return f"{today}.{next_n}"


def new_draft_from(parent: dict, today: str, existing_versions: List[str]) -> dict:
    """Derive a new draft from ``parent`` (append-only; never mutates parent)."""
    draft = copy.deepcopy(parent)
    draft["parent_version"] = parent.get("version")
    draft["version"] = next_version(today, existing_versions)
    draft["status"] = "draft"
    return draft
