"""Build a new project's starting baseline from a source (clone / template / import).

A schema-valid baseline can't be truly blank (source_documents / course_system /
evidence_index all require >=1 item), so a new project always starts from a
valid source and is relabelled into a fresh lineage (new id, version .1,
parent_version None, status draft). Validation/governance run at create time.
"""

from __future__ import annotations

import copy
from typing import Any, Dict


def prepare_new_baseline(source: Dict[str, Any], baseline_id: str, name: str, today: str) -> Dict[str, Any]:
    baseline = copy.deepcopy(source)
    baseline["baseline_id"] = baseline_id
    baseline.setdefault("project", {})["name"] = name
    baseline["version"] = f"{today}.1"
    baseline["parent_version"] = None
    baseline["status"] = "draft"
    return baseline
