"""Parsing and state model for the workflow progress protocol.

Pure logic (no Qt) so it can be unit-tested. The desktop window feeds each
stdout line to :func:`parse_progress_line`; recognised events drive a
:class:`ProgressModel`, and unrecognised lines are treated as ordinary log
output. See ``scripts/progress.py`` for the emitting side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

SENTINEL = "##DASH_PROGRESS##"

_STEP_TERMINAL = {"ok", "skip", "fail"}


@dataclass(frozen=True)
class ProgressEvent:
    kind: str  # "plan" | "stage" | "step" | "done"
    labels: Optional[List[str]] = None  # plan
    label: str = ""  # stage(unused) / step / done
    index: Optional[int] = None  # stage / step (1-based)
    total: Optional[int] = None  # step
    state: Optional[str] = None  # step: start|ok|skip|fail
    outcome: str = ""
    message: str = ""
    completed: int = 0


def parse_progress_line(line: str) -> Optional[ProgressEvent]:
    """Return a ProgressEvent if the line is a progress sentinel, else None."""
    stripped = line.strip()
    if not stripped.startswith(SENTINEL):
        return None
    payload_text = stripped[len(SENTINEL):].strip()
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    if kind == "plan":
        labels = payload.get("labels")
        if not isinstance(labels, list):
            return None
        return ProgressEvent(kind="plan", labels=[str(x) for x in labels])
    if kind == "stage":
        return ProgressEvent(kind="stage", index=_as_int(payload.get("i")))
    if kind == "step":
        return ProgressEvent(
            kind="step",
            label=str(payload.get("label", "")),
            index=_as_int(payload.get("i")),
            total=_as_int(payload.get("n")),
            state=str(payload.get("state", "start")),
        )
    if kind == "done":
        return ProgressEvent(kind="done", label=str(payload.get("label", "")))
    if kind == "result":
        outcome = str(payload.get("outcome", ""))
        if outcome not in {"prepared", "success", "partial", "failed", "cancelled"}:
            return None
        completed, total = _as_int(payload.get("completed")), _as_int(payload.get("total"))
        if completed is None or total is None or not 0 <= completed <= total:
            return None
        if outcome == "success" and (total == 0 or completed != total):
            return None
        return ProgressEvent(kind="result", label=str(payload.get("label", "")),
                             outcome=outcome, message=str(payload.get("message", "")),
                             completed=completed, total=total)
    return None


def _as_int(value: object) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# Stage status values used by the panel.
PENDING = "pending"
RUNNING = "running"
OK = "ok"
SKIP = "skip"
FAIL = "fail"


@dataclass
class StageState:
    label: str
    status: str = PENDING


@dataclass
class ProgressModel:
    """Accumulates progress events into a renderable state.

    ``has_signal`` stays False until any protocol event arrives, so callers can
    fall back to an indeterminate/busy display for scripts that emit nothing.
    """

    stages: List[StageState] = field(default_factory=list)
    has_signal: bool = False
    finished: bool = False
    done_label: str = ""
    # Inner loop (determinate bar) for the current stage.
    step_total: int = 0
    step_done: int = 0
    step_label: str = ""
    step_failed: bool = False
    had_failure: bool = False
    outcome: str = ""
    result_message: str = ""
    result_completed: int = 0
    result_total: int = 0

    def apply(self, event: ProgressEvent) -> None:
        self.has_signal = True
        if event.kind == "plan":
            self.stages = [StageState(label) for label in (event.labels or [])]
            self.had_failure = False
            self.outcome = ""
            self.finished = False
            self.done_label = ""
            self.result_message = ""
            self.result_completed = self.result_total = 0
            self._reset_steps()
        elif event.kind == "stage":
            self._advance_stage(event.index)
        elif event.kind == "step":
            self._apply_step(event)
        elif event.kind == "done":
            self.finished = True
            self.done_label = event.label
            if not self.had_failure and self.outcome in {"", "success", "prepared"}:
                self.mark_all_ok()
            if self.step_total:
                self.step_done = self.step_total
        elif event.kind == "result":
            self.finished = True
            self.done_label = event.label
            self.outcome = event.outcome
            self.result_message = event.message
            self.result_completed = event.completed
            self.result_total = event.total or 0
            if event.outcome in {"failed", "partial", "cancelled"}:
                self.had_failure = True
                self.mark_failed()

    def mark_all_ok(self) -> None:
        """成功收尾：未完成的阶段全部标记为完成。"""
        if self.had_failure:
            return
        for stage in self.stages:
            if stage.status in (PENDING, RUNNING):
                stage.status = OK

    def mark_failed(self) -> None:
        """失败收尾：把正在进行的阶段标记为失败（其后阶段保持待办）。"""
        for stage in self.stages:
            if stage.status == RUNNING:
                stage.status = FAIL
        self.step_failed = True
        self.had_failure = True

    def _reset_steps(self) -> None:
        self.step_total = 0
        self.step_done = 0
        self.step_label = ""
        self.step_failed = False

    def _advance_stage(self, index: Optional[int]) -> None:
        if index is None:
            return
        # 进入新阶段前把之前的阶段收尾；新阶段的循环计数清零。
        self._reset_steps()
        for position, stage in enumerate(self.stages, start=1):
            if position < index:
                if stage.status in (PENDING, RUNNING):
                    stage.status = OK
            elif position == index:
                stage.status = RUNNING
            else:
                break

    def _apply_step(self, event: ProgressEvent) -> None:
        if event.total:
            self.step_total = event.total
        if event.state == "start":
            self.step_label = event.label
            if event.index:
                self.step_done = max(self.step_done, event.index - 1)
        elif event.state in _STEP_TERMINAL:
            if event.index:
                self.step_done = max(self.step_done, event.index)
            if event.state == "fail":
                self.step_failed = True
                self.had_failure = True
                for stage in self.stages:
                    if stage.status == RUNNING:
                        stage.status = FAIL

    # -- Derived, read-only helpers for the panel --------------------------
    def current_stage_index(self) -> int:
        for position, stage in enumerate(self.stages, start=1):
            if stage.status == RUNNING:
                return position
        return 0

    def stage_count(self) -> int:
        return len(self.stages)

    def is_determinate(self) -> bool:
        return self.step_total > 0


def resolve_outcome(model: ProgressModel, exit_code: int, *, strict: bool = False,
                    cancelled: bool = False, crashed: bool = False) -> str:
    """Never infer image success from exit 0 without the result contract."""
    if cancelled:
        return "cancelled"
    if crashed:
        return "failed"
    if model.outcome in {"failed", "cancelled"}:
        return model.outcome
    if model.outcome == "partial":
        return "partial" if exit_code == 3 else "failed"
    if exit_code != 0 or model.had_failure:
        return "failed"
    if strict and not model.outcome:
        return "failed"
    return model.outcome or "success"
