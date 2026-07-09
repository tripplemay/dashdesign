"""Shared baseline builders for cloud tests (no cloud/server imports)."""

from __future__ import annotations

import copy
import json
from typing import Any, Dict

from app_runtime import baseline_path


def _bundled() -> Dict[str, Any]:
    return json.loads(baseline_path().read_text(encoding="utf-8"))


def base_baseline(baseline_id: str = "test_project", version: str = "2026.07.06.1") -> Dict[str, Any]:
    """A schema-valid, governance-clean draft baseline for the given id/version."""
    baseline = _bundled()
    baseline["baseline_id"] = baseline_id
    baseline["version"] = version
    baseline["parent_version"] = None
    baseline["status"] = "draft"
    baseline.setdefault("project", {})["name"] = f"{baseline_id} 项目"
    return baseline


def dirty(baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy that is schema-valid but violates B->C governance."""
    out = copy.deepcopy(baseline)
    messages = out.setdefault("consumer_baseline", {}).setdefault("core_messages", [])
    if messages:
        messages[0]["text"] = "保证一定升学名校，" + str(messages[0].get("text", ""))
    else:
        messages.append({"text": "保证一定升学名校", "confidence": 0.9, "evidence": []})
    return out
