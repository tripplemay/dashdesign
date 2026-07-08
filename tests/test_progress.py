"""Unit tests for the workflow progress protocol parser and model."""

from __future__ import annotations

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from ui.progress import (
    FAIL,
    OK,
    PENDING,
    RUNNING,
    SKIP,
    ProgressModel,
    parse_progress_line,
)

_EMITTER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "progress.py"


def _load_emitter():
    spec = importlib.util.spec_from_file_location("dashdesign_progress_emitter", _EMITTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class TestEmitter:
    def test_silent_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DASHDESIGN_PROGRESS", raising=False)
        emitter = _load_emitter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            emitter.plan(["a", "b"])
            emitter.stage(1)
            emitter.done("/tmp")
        assert buf.getvalue() == ""

    def test_emits_parseable_lines_with_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DASHDESIGN_PROGRESS", "1")
        emitter = _load_emitter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            emitter.plan(["扫描", "处理"])
            emitter.stage(2)
            emitter.step("海报.jpg", 1, 5, "ok")
            emitter.done("/tmp/out")
        events = [parse_progress_line(line) for line in buf.getvalue().splitlines()]
        events = [e for e in events if e is not None]
        assert [e.kind for e in events] == ["plan", "stage", "step", "done"]
        assert events[0].labels == ["扫描", "处理"]
        assert events[1].index == 2
        assert events[2].label == "海报.jpg" and events[2].total == 5 and events[2].state == "ok"
        assert events[3].label == "/tmp/out"

    def test_emitter_output_round_trips_through_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DASHDESIGN_PROGRESS", "1")
        emitter = _load_emitter()
        buf = io.StringIO()
        with redirect_stdout(buf):
            emitter.plan(["扫描", "处理", "完成"])
            emitter.stage(2)
            emitter.step("x", 1, 3, "start")
            emitter.step("x", 1, 3, "ok")
        model = ProgressModel()
        for line in buf.getvalue().splitlines():
            event = parse_progress_line(line)
            if event is not None:
                model.apply(event)
        assert model.stage_count() == 3
        assert model.stages[0].status == OK
        assert model.stages[1].status == RUNNING
        assert model.step_total == 3 and model.step_done == 1


class TestParse:
    def test_non_progress_line_returns_none(self) -> None:
        assert parse_progress_line("Package written to /tmp/out") is None
        assert parse_progress_line("") is None
        assert parse_progress_line("  regular log  ") is None

    def test_plan_event(self) -> None:
        ev = parse_progress_line('##DASH_PROGRESS## {"kind":"plan","labels":["扫描","处理","完成"]}')
        assert ev is not None
        assert ev.kind == "plan"
        assert ev.labels == ["扫描", "处理", "完成"]

    def test_stage_event(self) -> None:
        ev = parse_progress_line('##DASH_PROGRESS## {"kind":"stage","i":2}')
        assert ev is not None and ev.kind == "stage" and ev.index == 2

    def test_step_event(self) -> None:
        ev = parse_progress_line(
            '##DASH_PROGRESS## {"kind":"step","label":"海报1.jpg","i":1,"n":12,"state":"start"}'
        )
        assert ev is not None
        assert ev.kind == "step" and ev.label == "海报1.jpg"
        assert ev.index == 1 and ev.total == 12 and ev.state == "start"

    def test_done_event(self) -> None:
        ev = parse_progress_line('##DASH_PROGRESS## {"kind":"done","label":"/tmp/out"}')
        assert ev is not None and ev.kind == "done" and ev.label == "/tmp/out"

    def test_leading_whitespace_tolerated(self) -> None:
        ev = parse_progress_line('   ##DASH_PROGRESS## {"kind":"stage","i":1}  ')
        assert ev is not None and ev.index == 1

    def test_malformed_json_returns_none(self) -> None:
        assert parse_progress_line("##DASH_PROGRESS## not json") is None
        assert parse_progress_line("##DASH_PROGRESS## [1,2,3]") is None

    def test_unknown_kind_returns_none(self) -> None:
        assert parse_progress_line('##DASH_PROGRESS## {"kind":"bogus"}') is None


class TestModel:
    def _feed(self, model: ProgressModel, line: str) -> None:
        ev = parse_progress_line(line)
        assert ev is not None
        model.apply(ev)

    def test_empty_model_has_no_signal(self) -> None:
        model = ProgressModel()
        assert not model.has_signal
        assert not model.is_determinate()
        assert model.stage_count() == 0

    def test_plan_builds_stages_all_pending(self) -> None:
        model = ProgressModel()
        self._feed(model, '##DASH_PROGRESS## {"kind":"plan","labels":["扫描","处理","完成"]}')
        assert model.has_signal
        assert [s.label for s in model.stages] == ["扫描", "处理", "完成"]
        assert all(s.status == PENDING for s in model.stages)

    def test_stage_advance_marks_previous_ok(self) -> None:
        model = ProgressModel()
        self._feed(model, '##DASH_PROGRESS## {"kind":"plan","labels":["扫描","处理","完成"]}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"stage","i":2}')
        assert model.stages[0].status == OK
        assert model.stages[1].status == RUNNING
        assert model.stages[2].status == PENDING
        assert model.current_stage_index() == 2

    def test_step_progress_determinate(self) -> None:
        model = ProgressModel()
        self._feed(model, '##DASH_PROGRESS## {"kind":"plan","labels":["处理"]}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"stage","i":1}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"step","label":"a.jpg","i":1,"n":3,"state":"start"}')
        assert model.is_determinate()
        assert model.step_total == 3
        assert model.step_done == 0
        assert model.step_label == "a.jpg"
        self._feed(model, '##DASH_PROGRESS## {"kind":"step","label":"a.jpg","i":1,"n":3,"state":"ok"}')
        assert model.step_done == 1
        self._feed(model, '##DASH_PROGRESS## {"kind":"step","label":"b.jpg","i":2,"n":3,"state":"start"}')
        assert model.step_done == 1 and model.step_label == "b.jpg"

    def test_step_fail_tracked(self) -> None:
        model = ProgressModel()
        self._feed(model, '##DASH_PROGRESS## {"kind":"plan","labels":["处理"]}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"stage","i":1}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"step","label":"x","i":1,"n":2,"state":"fail"}')
        assert model.step_failed
        assert model.step_done == 1

    def test_stage_advance_resets_step_counter(self) -> None:
        model = ProgressModel()
        self._feed(model, '##DASH_PROGRESS## {"kind":"plan","labels":["处理","后处理"]}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"stage","i":1}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"step","label":"x","i":3,"n":3,"state":"ok"}')
        assert model.step_done == 3
        self._feed(model, '##DASH_PROGRESS## {"kind":"stage","i":2}')
        assert model.step_total == 0 and model.step_done == 0

    def test_done_marks_all_ok(self) -> None:
        model = ProgressModel()
        self._feed(model, '##DASH_PROGRESS## {"kind":"plan","labels":["扫描","处理","完成"]}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"stage","i":2}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"done","label":"/tmp/out"}')
        assert model.finished
        assert model.done_label == "/tmp/out"
        assert all(s.status == OK for s in model.stages)

    def test_skip_state_terminal(self) -> None:
        model = ProgressModel()
        self._feed(model, '##DASH_PROGRESS## {"kind":"plan","labels":["处理"]}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"stage","i":1}')
        self._feed(model, '##DASH_PROGRESS## {"kind":"step","label":"x","i":1,"n":4,"state":"skip"}')
        assert model.step_done == 1
        assert not model.step_failed
