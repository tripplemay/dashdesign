"""Baseline JSON-schema loading and validation."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import List, Optional

import jsonschema

from app_runtime import baseline_schema_path


@functools.lru_cache(maxsize=4)
def _load_schema(schema_path_str: str) -> dict:
    return json.loads(Path(schema_path_str).read_text(encoding="utf-8"))


def load_schema(schema_path: Optional[Path] = None) -> dict:
    path = schema_path or baseline_schema_path()
    return _load_schema(str(path))


def validation_errors(baseline: dict, schema_path: Optional[Path] = None) -> List[str]:
    """Return human-readable schema errors (empty list == valid)."""
    schema = load_schema(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    messages: List[str] = []
    for error in sorted(validator.iter_errors(baseline), key=lambda e: list(e.path)):
        location = "/".join(str(p) for p in error.path) or "(根)"
        messages.append(f"{location}: {error.message}")
    return messages


def is_valid(baseline: dict, schema_path: Optional[Path] = None) -> bool:
    return not validation_errors(baseline, schema_path)
