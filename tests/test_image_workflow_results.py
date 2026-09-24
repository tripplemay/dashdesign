"""Mocked image API through durable workflow results and GUI interpretation."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import full_poster_image2 as full
import gpt_image_rebuild as rebuild
import image_api_client as api
import text_to_image_print as t2i
from workflow_result import create_package_dir, finish, package_exit_code, run_step
from ui.progress import ProgressEvent, ProgressModel, parse_progress_line, resolve_outcome


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (16, 32), "blue").save(buffer, format="PNG")
    return buffer.getvalue()


def response(code=200, data=None):
    value = Mock(status_code=code, headers={"x-request-id": "req-test"})
    value.json.return_value = data
    return value


@pytest.fixture(autouse=True)
def no_paid_requests(monkeypatch):
    monkeypatch.setenv("DASHDESIGN_PROGRESS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-private-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://sandbox.invalid/v1")
    monkeypatch.setattr(requests, "post", Mock(side_effect=AssertionError("Unexpected POST")))
    monkeypatch.setattr(requests, "get", Mock(side_effect=AssertionError("Unexpected GET")))
    monkeypatch.setattr(full, "translate_visual_prompt", lambda prompt, model: prompt)
    monkeypatch.setattr(t2i, "translate_visual_prompt", lambda prompt, model: prompt)


@pytest.mark.parametrize("code", [400, 401, 403, 404, 429, 500, 502])
def test_http_errors_are_sanitized_without_retry(code, tmp_path, monkeypatch):
    post = Mock(return_value=response(code, {"error": {"code": "model_not_found", "message": "bad sk-private-test https://signed.invalid/?secret=1"}}))
    monkeypatch.setattr(requests, "post", post)
    result = api.execute_image_generation({"model": "custom-image"}, tmp_path / "out.png")
    assert result["status"] == "error" and result["status_code"] == code
    assert result["model"] == "custom-image" and result["request_id"] == "req-test"
    assert "sk-private-test" not in json.dumps(result) and "secret=1" not in json.dumps(result)
    assert post.call_count == 1
    assert not (tmp_path / "out.png").exists()


@pytest.mark.parametrize("body", [{}, {"data": []}, {"data": [{}]}, {"data": [{"b64_json": "bad!"}]}, {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]}, None])
def test_invalid_response_never_creates_an_image(body, tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "post", Mock(return_value=response(data=body)))
    assert api.execute_image_generation({}, tmp_path / "out.png")["status"] == "error"
    assert not list(tmp_path.iterdir())


def test_timeout_bad_json_and_download_failure(tmp_path, monkeypatch):
    for exception in (requests.Timeout("timeout sk-private-test"), requests.ConnectionError("offline")):
        post = Mock(side_effect=exception)
        monkeypatch.setattr(requests, "post", post)
        result = api.execute_image_generation({}, tmp_path / "out.png")
        assert result["status"] == "error" and result["execution_uncertain"]
        assert post.call_count == 1
    bad = response()
    bad.json.side_effect = ValueError("invalid JSON")
    monkeypatch.setattr(requests, "post", Mock(return_value=bad))
    assert api.execute_image_generation({}, tmp_path / "out.png")["status"] == "error"
    monkeypatch.setattr(requests, "get", Mock(side_effect=requests.HTTPError("403 https://signed.invalid/?token=x")))
    result = api.write_image_response({"data": [{"url": "https://signed.invalid/?token=x"}]}, tmp_path / "out.png")
    assert result["status"] == "error" and "token=x" not in str(result)


@pytest.mark.parametrize("mode", ["generate", "edit"])
def test_valid_image_and_model_reach_api(mode, tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    source.write_bytes(png_bytes())
    post = Mock(return_value=response(data={"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]}))
    monkeypatch.setattr(requests, "post", post)
    result = api.execute_image_request(source, {"model": "custom-image"}, tmp_path / "out.png", mode)
    assert result["status"] == "generated"
    assert post.call_args.kwargs["data" if mode == "edit" else "json"]["model"] == "custom-image"
    assert (tmp_path / "out.png").read_bytes() == png_bytes()


@pytest.mark.parametrize("statuses,execute,outcome,code", [
    ([["error"]], True, "failed", 1), ([["generated"]], True, "success", 0),
    ([["generated"], ["error"]], True, "partial", 3),
    ([["generated", "error"]], True, "partial", 3),
    ([["cancelled"]], True, "cancelled", 130),
    ([["prepared_not_executed"]], False, "prepared", 0),
])
def test_result_contract(statuses, execute, outcome, code, tmp_path, capsys):
    state = {}
    finish(tmp_path, state, execute, [[{"status": value} for value in unit] for unit in statuses])
    assert package_exit_code(tmp_path) == code
    assert state["result"]["outcome"] == outcome
    model = ProgressModel()
    for line in capsys.readouterr().out.splitlines():
        event = parse_progress_line(line)
        if event:
            model.apply(event)
    assert resolve_outcome(model, code, strict=True) == outcome


def test_failure_cannot_be_erased_by_later_stage_or_done():
    model = ProgressModel()
    for event in (ProgressEvent("plan", labels=["API", "Summary"]), ProgressEvent("stage", index=1),
                  ProgressEvent("step", state="fail"), ProgressEvent("stage", index=2), ProgressEvent("done")):
        model.apply(event)
    assert model.had_failure and model.stages[0].status == "fail"
    assert resolve_outcome(model, 0) == "failed"
    assert resolve_outcome(ProgressModel(), 0, strict=True) == "failed"
    assert resolve_outcome(ProgressModel(outcome="success"), 0, strict=True, crashed=True) == "failed"
    assert resolve_outcome(model, 0, cancelled=True) == "cancelled"


def test_validate_artifacts_and_preserve_previous_package(tmp_path):
    assert run_step(lambda: {"status": "generated", "output": str(tmp_path / "missing.png")})["status"] == "error"
    def cancel():
        raise KeyboardInterrupt()
    assert run_step(cancel)["status"] == "cancelled"
    first = create_package_dir(tmp_path / "pkg")
    assert create_package_dir(tmp_path / "pkg") != first


def run_full(tmp_path, monkeypatch, *, execute=True, postprocess=False, candidates=1):
    args = ["full_poster_image2.py", "--output-dir", str(tmp_path), "--width-cm", "8", "--height-cm", "18",
            "--poster-copy", "主标题：学习AI", "--prompt", "classroom", "--model", "custom-image", "--candidates", str(candidates)]
    if execute:
        args.append("--execute")
    if postprocess:
        args.append("--postprocess-print")
    monkeypatch.setattr(sys, "argv", args)
    code = full.main()
    package = next(tmp_path.glob("*full_poster_image2"))
    return code, json.loads((package / "status.json").read_text()), package


def test_full_poster_404_exits_nonzero(tmp_path, monkeypatch):
    post = Mock(return_value=response(404, {"error": {"code": "model_not_found", "message": "unsupported model"}}))
    monkeypatch.setattr(requests, "post", post)
    code, state, package = run_full(tmp_path, monkeypatch)
    assert code == 1 and state["result"]["outcome"] == "failed"
    assert json.loads((package / "generation_record.json").read_text())["model"] == "custom-image"
    assert not list(package.rglob("*.png"))


@pytest.mark.parametrize("postprocess", [False, True])
def test_full_poster_retains_partial_artifact_and_checkpoints(tmp_path, monkeypatch, postprocess):
    count = 0
    def generate(payload, output):
        nonlocal count
        count += 1
        saved = json.loads((output.parent.parent / "status.json").read_text())
        assert len(saved["image_generation"]) == count
        if count == 2:
            assert saved["image_generation"][0]["status"] == "generated"
            return {"status": "error", "reason": "second failed"}
        output.write_bytes(png_bytes())
        return {"status": "generated", "output": str(output)}
    monkeypatch.setattr(full, "execute_image_generation", generate)
    monkeypatch.setattr(full, "prepare_print_output", Mock(side_effect=OSError("disk full")))
    code, state, package = run_full(tmp_path, monkeypatch, postprocess=postprocess, candidates=1 if postprocess else 2)
    assert code == 3 and state["result"]["outcome"] == "partial"
    assert len(list(package.rglob("*.png"))) == 1


def test_full_offline_is_prepared(tmp_path, monkeypatch):
    code, state, _ = run_full(tmp_path, monkeypatch, execute=False)
    assert code == 0 and state["result"]["outcome"] == "prepared"


@pytest.mark.parametrize("worker", ["text-image", "gpt"])
def test_other_worker_failure_exit_and_model(tmp_path, monkeypatch, worker):
    post = Mock(return_value=response(404, {"error": {"code": "model_not_found"}}))
    monkeypatch.setattr(requests, "post", post)
    if worker == "gpt":
        source = tmp_path / "source_8x18.png"
        source.write_bytes(png_bytes())
        args = [str(source), "--api-mode", "edit"]
        module = rebuild
    else:
        args = ["--width-cm", "8", "--height-cm", "18", "--prompt", "classroom"]
        module = t2i
    monkeypatch.setattr(sys, "argv", [worker, *args, "--output-dir", str(tmp_path / "out"), "--model", "custom-image", "--execute"])
    assert module.main() == 1
    assert post.call_count == 1
    assert post.call_args.kwargs["data" if worker == "gpt" else "json"]["model"] == "custom-image"


@pytest.mark.parametrize("outcome,code,cancelled,banner", [
    ("failed", 1, False, "error"), ("", 0, False, "error"),
    ("partial", 3, False, "warning"), ("success", 0, True, "warning"),
    ("success", 0, False, "success"),
])
def test_gui_completion_uses_result_not_just_exit_code(outcome, code, cancelled, banner):
    from PySide6.QtCore import QProcess
    from ui.main_window import DashDesignQtApp
    window = Mock()
    window._progress = ProgressModel(outcome=outcome)
    window._running_worker = "full-poster"
    window._running_title = "Poster"
    window._cancel_requested = cancelled
    window._stderr_tail = []
    window._collect_to_workspace.return_value = []
    DashDesignQtApp.process_finished(window, code, QProcess.ExitStatus.NormalExit)
    assert window.banner.show_message.call_args.args[0] == banner
    if outcome != "success" or cancelled:
        window._collect_to_workspace.assert_not_called()


def test_cancelled_step_stops_candidate_loop(tmp_path, monkeypatch):
    def cancel(*args):
        raise KeyboardInterrupt()
    generate = Mock(side_effect=cancel)
    monkeypatch.setattr(full, "execute_image_generation", generate)
    code, state, _ = run_full(tmp_path, monkeypatch, candidates=3)
    assert code == 130 and state["result"]["outcome"] == "cancelled"
    assert generate.call_count == 1
    assert state["result"]["total"] == 3


def test_full_success_with_print_output(tmp_path, monkeypatch):
    monkeypatch.setattr(requests, "post", Mock(return_value=response(data={"data": [{"b64_json": base64.b64encode(png_bytes()).decode()}]})))
    code, state, package = run_full(tmp_path, monkeypatch, postprocess=True)
    assert code == 0 and state["result"]["outcome"] == "success"
    assert state["print_output"][0]["status"] == "generated"
    assert list(package.rglob("*.jpg"))


def test_missing_key_is_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    code, state, _ = run_full(tmp_path, monkeypatch)
    assert code == 1 and state["image_generation"][0]["error_type"] == "missing_api_key"


def test_desktop_worker_subprocess_404_contract(tmp_path):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps({"error": {"code": "model_not_found", "message": "No configured account"}}).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {**os.environ, "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "OPENAI_API_KEY": "fake-worker-key", "DASHDESIGN_PROGRESS": "1"}
        result = subprocess.run(
            [sys.executable, str(ROOT / "desktop_qt_app.py"), "--worker", "full-poster",
             "--output-dir", str(tmp_path), "--width-cm", "8", "--height-cm", "18",
             "--prompt", "classroom", "--poster-copy", "主标题：学习AI", "--candidates", "1",
             "--model", "worker-image", "--execute"],
            env=env, cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 1, result.stderr
    assert len(received) == 1 and received[0]["model"] == "worker-image"
    events = [parse_progress_line(line) for line in result.stdout.splitlines()]
    final = next(event for event in events if event and event.kind == "result")
    assert final.outcome == "failed"
    assert not any(event and event.kind == "done" for event in events)
    state = json.loads((Path(final.label) / "status.json").read_text())
    assert state["result"]["exit_code"] == 1
    assert "fake-worker-key" not in result.stdout + result.stderr
