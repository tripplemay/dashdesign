#!/usr/bin/env python3
"""Prompt template helpers for DashDesign image generation workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_TEMPLATE_LIBRARY = Path("docs") / "prompt_templates" / "full_poster_templates.v1.json"


def load_template_library(path: Path = DEFAULT_TEMPLATE_LIBRARY) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Prompt template library not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Prompt template library is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Prompt template library root must be an object")
    return payload


def _default_id(library: dict[str, Any], key: str) -> str:
    defaults = library.get("defaults", {})
    if isinstance(defaults, dict):
        value = str(defaults.get(key, "")).strip()
        if value:
            return value
    return ""


def _find_template(library: dict[str, Any], collection_name: str, template_id: str) -> dict[str, Any]:
    collection = library.get(collection_name, [])
    if not isinstance(collection, list):
        raise ValueError(f"Prompt template collection must be a list: {collection_name}")
    for item in collection:
        if isinstance(item, dict) and str(item.get("id", "")) == template_id:
            return item
    available = ", ".join(str(item.get("id", "")) for item in collection if isinstance(item, dict))
    raise ValueError(f"Unknown prompt template id for {collection_name}: {template_id}. Available: {available}")


def _text_blocks(template: dict[str, Any], key: str) -> list[str]:
    values = template.get(key, [])
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def template_options(library: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    mapping = {
        "purposes": "purpose",
        "styles": "style",
        "layouts": "layout",
        "text_density": "text_density",
        "negative_templates": "negative",
    }
    options: dict[str, list[dict[str, str]]] = {}
    for collection_name, output_key in mapping.items():
        collection = library.get(collection_name, [])
        if not isinstance(collection, list):
            options[output_key] = []
            continue
        options[output_key] = [
            {"id": str(item.get("id", "")), "name": str(item.get("name", ""))}
            for item in collection
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
    return options


def compile_prompt_template_profile(
    library: dict[str, Any],
    purpose_id: str | None = None,
    style_id: str | None = None,
    layout_id: str | None = None,
    text_density_id: str | None = None,
    negative_id: str | None = None,
) -> dict[str, Any]:
    purpose_id = (purpose_id or _default_id(library, "purpose")).strip()
    style_id = (style_id or _default_id(library, "style")).strip()
    layout_id = (layout_id or _default_id(library, "layout")).strip()
    text_density_id = (text_density_id or _default_id(library, "text_density")).strip()
    negative_id = (negative_id or _default_id(library, "negative")).strip()

    purpose = _find_template(library, "purposes", purpose_id)
    style = _find_template(library, "styles", style_id)
    layout = _find_template(library, "layouts", layout_id)
    text_density = _find_template(library, "text_density", text_density_id)
    negative = _find_template(library, "negative_templates", negative_id)

    positive_blocks = [
        *_text_blocks(purpose, "positive_blocks"),
        *_text_blocks(style, "positive_blocks"),
        *_text_blocks(layout, "positive_blocks"),
        *_text_blocks(text_density, "positive_blocks"),
    ]
    typography_blocks = _text_blocks(style, "typography_blocks")
    cta_guidance = _text_blocks(purpose, "cta_guidance")
    negative_blocks = _text_blocks(negative, "blocks")
    risk_notes = [str(text_density.get("risk_note", "")).strip()]
    risk_notes = [item for item in risk_notes if item]

    return {
        "library_version": str(library.get("version", "")),
        "ids": {
            "purpose": purpose_id,
            "style": style_id,
            "layout": layout_id,
            "text_density": text_density_id,
            "negative": negative_id,
        },
        "labels": {
            "purpose": str(purpose.get("name", "")),
            "style": str(style.get("name", "")),
            "layout": str(layout.get("name", "")),
            "text_density": str(text_density.get("name", "")),
            "negative": str(negative.get("name", "")),
        },
        "purpose_goal": str(purpose.get("goal", "")),
        "positive_blocks": positive_blocks,
        "typography_blocks": typography_blocks,
        "cta_guidance": cta_guidance,
        "negative_blocks": negative_blocks,
        "risk_notes": risk_notes,
    }
