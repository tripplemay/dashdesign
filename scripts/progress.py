"""Structured progress protocol for DashDesign workflow scripts.

Scripts emit machine-readable progress lines on stdout that the desktop GUI
parses into a graphical progress panel. Emission is gated on the
``DASHDESIGN_PROGRESS=1`` environment variable (set by the GUI) so running the
scripts directly from a terminal keeps their output unchanged.

Protocol (one JSON object per line, prefixed with a unique sentinel):

    ##DASH_PROGRESS## {"kind": "plan", "labels": ["扫描图片", "逐张处理", "完成"]}
    ##DASH_PROGRESS## {"kind": "stage", "i": 2}
    ##DASH_PROGRESS## {"kind": "step", "label": "海报1.jpg", "i": 1, "n": 12, "state": "start"}
    ##DASH_PROGRESS## {"kind": "step", "label": "海报1.jpg", "i": 1, "n": 12, "state": "ok"}
    ##DASH_PROGRESS## {"kind": "done", "label": "<输出目录>"}

- ``plan``  declares the full ordered stage list once, up front.
- ``stage`` advances to stage ``i`` (1-based); earlier stages become done.
- ``step``  drives a determinate bar inside a loop stage. ``state`` is one of
  ``start`` / ``ok`` / ``skip`` / ``fail``.
- ``done``  marks successful completion (``label`` is the output directory).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable

SENTINEL = "##DASH_PROGRESS##"


def _enabled() -> bool:
    return os.environ.get("DASHDESIGN_PROGRESS") == "1"


def _emit(payload: dict) -> None:
    if not _enabled():
        return
    try:
        sys.stdout.write(f"{SENTINEL} {json.dumps(payload, ensure_ascii=False)}\n")
        sys.stdout.flush()
    except (OSError, ValueError):
        # 进度输出绝不能影响工作流本身。
        pass


def plan(labels: Iterable[str]) -> None:
    _emit({"kind": "plan", "labels": [str(label) for label in labels]})


def stage(index: int) -> None:
    _emit({"kind": "stage", "i": int(index)})


def step(label: str, index: int, total: int, state: str = "start") -> None:
    _emit({"kind": "step", "label": str(label), "i": int(index), "n": int(total), "state": state})


def done(label: str = "") -> None:
    _emit({"kind": "done", "label": str(label)})
