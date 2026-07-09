"""Built-in Chinese visual-prompt presets for the text-to-image page.

Qt-free loader so it is unit-testable without a QApplication. The presets are
shown to the user in Chinese and inserted verbatim into the prompt box; the
generation scripts translate them to English before calling the image model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app_runtime import scene_prompts_library_path


@dataclass(frozen=True)
class ScenePrompt:
    id: str
    label: str
    category: str
    prompt: str


def load_scene_prompts(path: "Optional[Path]" = None) -> list[ScenePrompt]:
    """Load the built-in scene presets.

    Tolerant by design: a missing, unreadable, or malformed library must never
    break the page — it simply yields no chips. Entries missing id/label/prompt
    or duplicating an already-seen id are skipped.
    """
    source = Path(path) if path is not None else scene_prompts_library_path()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    scenes = payload.get("scenes") if isinstance(payload, dict) else None
    if not isinstance(scenes, list):
        return []

    result: list[ScenePrompt] = []
    seen: set[str] = set()
    for item in scenes:
        if not isinstance(item, dict):
            continue
        scene_id = str(item.get("id", "")).strip()
        label = str(item.get("label", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        category = str(item.get("category", "")).strip()
        if not scene_id or not label or not prompt or scene_id in seen:
            continue
        seen.add(scene_id)
        result.append(ScenePrompt(id=scene_id, label=label, category=category, prompt=prompt))
    return result
