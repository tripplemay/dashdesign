"""No paid calls: multimodal interpretation, durable resumes, and UI integration."""

from __future__ import annotations

import base64
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edit_prompt_agent as agent
import edit_prompt_workflow as workflow
import gpt_image_rebuild as gpt
from workflow_result import package_exit_code
from ui.progress import ProgressModel, parse_progress_line, resolve_outcome


def ready(**updates):
    value = {"status": "ready", "summary": "只把背景改为蓝色，保留其他内容。",
             "actions": [{"target": "background", "location": "behind the content", "instruction": "Change to blue.", "scope": "local"}],
             "preserve": ["Keep all foreground elements and text unchanged."], "text_changes": [], "question": ""}
    value.update(updates)
    return value


def question():
    return {"status": "needs_input", "summary": "需要确认修改对象。", "actions": [], "preserve": [],
            "text_changes": [], "question": "要修改左边还是右边的标题？"}


def response(value, code=200):
    result = Mock(status_code=code, headers={"x-request-id": "test-request"})
    result.json.return_value = value
    return result


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "poster_8x18.png"
    Image.new("RGB", (32, 72), "white").save(path)
    return path


@pytest.fixture(autouse=True)
def network(monkeypatch, source):
    monkeypatch.setenv("OPENAI_API_KEY", "test-private-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://sandbox.invalid/v1")
    monkeypatch.setenv("DASHDESIGN_PROGRESS", "1")
    image_data = base64.b64encode(source.read_bytes()).decode("ascii")
    def send(url, **kwargs):
        if url.endswith("/chat/completions"):
            return response({"choices": [{"message": {"content": json.dumps(ready(), ensure_ascii=False)}}]})
        assert url.endswith("/images/edits")
        return response({"data": [{"b64_json": image_data}]})
    post = Mock(side_effect=send)
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(requests, "get", Mock(side_effect=AssertionError("Unexpected GET")))
    return post


def run(source, tmp_path, **kwargs):
    options = dict(source=source, output_dir=tmp_path / "out", print_dpi=200,
                   description="把背景改成蓝色", execute=True, api_mode="edit",
                   model="image-test", optimize_prompt=True, agent_model="vision-test")
    options.update(kwargs)
    return gpt.build_package(**options)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def answer(package, text="左侧标题"):
    state = read(package / "edit_state.json")
    path = package / "answer.json"
    path.write_text(json.dumps({"revision": state["revision"], "answer": text}, ensure_ascii=False), encoding="utf-8")
    return path


def test_one_agent_then_one_image(source, tmp_path, network, capsys):
    package = run(source, tmp_path)
    assert package_exit_code(package) == 0
    assert network.call_count == 2
    first, second = network.call_args_list
    assert first.args[0].endswith("/chat/completions")
    assert first.kwargs["timeout"] == 90
    content = first.kwargs["json"]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert first.kwargs["json"]["model"] == "vision-test"
    assert second.kwargs["data"]["model"] == "image-test"
    assert Path(second.kwargs["files"]["image"].name) != source
    assert "test-private-key" not in (package / "edit_state.json").read_text(encoding="utf-8")
    model = ProgressModel()
    events = []
    for line in capsys.readouterr().out.splitlines():
        event = parse_progress_line(line)
        if event:
            events.append(event)
            model.apply(event)
    assert any(event.kind == "interpretation" and event.prompt for event in events)
    assert resolve_outcome(model, 0, strict=True) == "success"


def test_offline_has_no_calls(source, tmp_path, network):
    package = run(source, tmp_path, execute=False)
    network.assert_not_called()
    assert read(package / "status.json")["result"]["outcome"] == "prepared"
    assert not (package / "image_edit_request.json").exists()
    assert "待 Agent" in (package / "prompt.md").read_text(encoding="utf-8")
    (package / "source_preview.jpg").write_bytes(b"stale preview")
    run(None, tmp_path, resume_package=package)
    assert network.call_count == 2 and package_exit_code(package) == 0
    with Image.open(package / "source_preview.jpg") as preview:
        preview.verify()


def test_repeated_clarification_keeps_history(source, tmp_path, network, monkeypatch):
    interpret = Mock(side_effect=[question(), question(), ready()])
    monkeypatch.setattr(workflow, "interpret_edit", interpret)
    package = run(source, tmp_path)
    run(None, tmp_path, resume_package=package, answer_file=answer(package, "左侧"))
    assert package_exit_code(package) == 4
    network.assert_not_called()
    run(None, tmp_path, resume_package=package, answer_file=answer(package, "改成学习AI"))
    assert len(interpret.call_args.args[2]) == 2
    assert network.call_count == 1 and package_exit_code(package) == 0


def test_clarification_resume_freezes_input_and_stops_duplicate(source, tmp_path, network, monkeypatch, capsys):
    interpret = Mock(side_effect=[question(), ready()])
    monkeypatch.setattr(workflow, "interpret_edit", interpret)
    package = run(source, tmp_path)
    assert package_exit_code(package) == 4
    network.assert_not_called()
    events = [parse_progress_line(line) for line in capsys.readouterr().out.splitlines()]
    model = ProgressModel()
    for event in events:
        if event:
            model.apply(event)
    assert resolve_outcome(model, 4, strict=True) == "needs_input"
    assert not model.had_failure
    assert model.stages[1].status == "waiting"
    original_hash = read(package / "edit_state.json")["source_sha256"]
    Image.new("RGB", (32, 72), "red").save(source)
    result = run(None, tmp_path, resume_package=package, answer_file=answer(package))
    assert result == package and package_exit_code(package) == 0
    assert read(package / "edit_state.json")["source_sha256"] == original_hash
    assert interpret.call_args.args[2][0]["answer"] == "左侧标题"
    assert network.call_count == 1
    with pytest.raises(ValueError, match="重复提交"):
        run(None, tmp_path, resume_package=package)
    assert network.call_count == 1


@pytest.mark.parametrize("failure", [requests.Timeout("test-private-key"), ValueError("bad JSON"), KeyboardInterrupt()])
def test_agent_failure_or_cancel_never_calls_image(source, tmp_path, network, monkeypatch, failure):
    monkeypatch.setattr(workflow, "interpret_edit", Mock(side_effect=failure))
    package = run(source, tmp_path)
    network.assert_not_called()
    assert package_exit_code(package) == (130 if isinstance(failure, KeyboardInterrupt) else 1)
    assert not read(package / "edit_state.json")["image_request_started"]
    assert "test-private-key" not in (package / "status.json").read_text(encoding="utf-8")


def test_retry_only_agent_after_failure(source, tmp_path, network, monkeypatch):
    monkeypatch.setattr(workflow, "interpret_edit", Mock(side_effect=[requests.Timeout("timeout"), ready()]))
    package = run(source, tmp_path)
    assert read(package / "edit_state.json")["phase"] == "agent_failed"
    run(None, tmp_path, resume_package=package)
    assert package_exit_code(package) == 0 and network.call_count == 1


def test_image_failure_is_not_partial_or_resumable(source, tmp_path, network):
    network.side_effect = [response({"choices": [{"message": {"content": json.dumps(ready())}}]}),
                           response({"error": {"message": "image unavailable"}}, 404)]
    package = run(source, tmp_path)
    assert read(package / "status.json")["result"]["outcome"] == "failed"
    with pytest.raises(ValueError, match="重复提交"):
        run(None, tmp_path, resume_package=package)
    assert network.call_count == 2


def test_stale_answers_modified_snapshot_and_lock_are_rejected(source, tmp_path, network, monkeypatch):
    monkeypatch.setattr(workflow, "interpret_edit", Mock(return_value=question()))
    package = run(source, tmp_path)
    reply = answer(package)
    reply.write_text('{"revision": 0, "answer": "left"}', encoding="utf-8")
    with pytest.raises(ValueError, match="过期"):
        run(None, tmp_path, resume_package=package, answer_file=reply)
    with workflow.task_lock(package):
        with pytest.raises(ValueError, match="正在运行"):
            run(None, tmp_path, resume_package=package, answer_file=answer(package))
    (package / read(package / "edit_state.json")["source_file"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="原图已变更"):
        run(None, tmp_path, resume_package=package, answer_file=answer(package))
    network.assert_not_called()


@pytest.mark.parametrize("code", [400, 401, 403, 429, 500])
def test_agent_http_failure_no_fallback(source, tmp_path, network, code):
    network.return_value = response({"error": {"message": "vision unavailable test-private-key"}}, code)
    network.side_effect = None
    package = run(source, tmp_path)
    assert network.call_count == 1 and package_exit_code(package) == 1


@pytest.mark.parametrize("data", [{}, ready(actions=[]), ready(question="unexpected"),
                                  ready(actions=[{"target": "x"}]), {**question(), "actions": ready()["actions"]}])
def test_invalid_structure_stops_edit(source, tmp_path, network, data):
    network.side_effect = None
    network.return_value = response({"choices": [{"message": {"content": json.dumps(data)}}]})
    package = run(source, tmp_path)
    assert package_exit_code(package) == 1 and network.call_count == 1


def test_literal_text_preserved_and_invented_copy_rejected():
    text = "把标题改成‘学习AI的好处’，价格改成‘88.00元’。"
    intent = ready(text_changes=[{"source_text": "旧标题", "replacement_text": "学习AI的好处", "user_quote": text},
                                {"source_text": "68元", "replacement_text": "88.00元", "user_quote": text}])
    compiled = agent.compile_edit_prompt(agent.validate_intent(intent, [text]), "1024x1536")
    assert "学习AI的好处" in compiled and "88.00元" in compiled
    intent["text_changes"][0]["replacement_text"] = "Benefits of AI"
    with pytest.raises(ValueError, match="invented"):
        agent.validate_intent(intent, [text])


def test_multiple_global_edits_do_not_inherit_old_prohibitions():
    intent = ready(actions=[{"target": "whole poster", "location": "entire canvas", "instruction": "Restyle as a neon classroom.", "scope": "global"},
                            {"target": "four section headings", "location": "all four sections", "instruction": "Align their left edges.", "scope": "global"},
                            {"target": "QR area", "location": "bottom", "instruction": "Remove and redistribute content into the freed area.", "scope": "local"}])
    prompt = agent.compile_edit_prompt(agent.validate_intent(intent, ["换风格、对齐、去二维码并重排"]), "1024x1536")
    assert "single modification" not in prompt and "pixel-identical" not in prompt
    assert "Restyle" in prompt and "Align" in prompt and "Remove" in prompt


def test_settings_and_ui_clarification(source, tmp_path, monkeypatch):
    from ui import api_config
    from ui.pages.gpt_page import GptPage
    from ui.commands import build_gpt_command
    monkeypatch.setattr(api_config, "load_edit_agent_model", lambda: "vision-test")
    monkeypatch.setattr(api_config, "load_image_model", lambda: "image-test")
    page = GptPage()
    page.gpt_source.setText(str(source))
    page.gpt_description.setPlainText("修改标题")
    page.prepare_run()
    package = tmp_path / "pkg"
    package.mkdir()
    workflow.save_json(package / "edit_state.json", {"phase": "needs_input", "question": "哪个标题？", "revision": 1})
    page.show_interpretation("确认标题", "")
    page.finish_edit(package, "needs_input")
    with pytest.raises(ValueError, match="先回答"):
        page.prepare_run()
    page.clarification_answer.setPlainText("第四个标题")
    page.prepare_run()
    form = page.form()
    command, _, _ = build_gpt_command(form)
    assert "--optimize-prompt" in command and "--resume-package" in command
    assert read(Path(form.answer_file)) == {"revision": 1, "answer": "第四个标题"}
    callback = Mock()
    page.resumeRequested.connect(callback)
    page.set_running(True)
    page.resume_button.click()
    callback.assert_not_called()
    page.set_running(False)
    page.resume_button.click()
    callback.assert_called_once()
    page.gpt_description.setPlainText("换成漫画风格")
    page.prepare_run()
    assert not page.form().resume_package
    assert page.gpt_description.toPlainText() == "换成漫画风格"
    page.close()


def test_gui_needs_input_is_not_failure_or_success(tmp_path):
    from PySide6.QtCore import QProcess
    from ui.main_window import DashDesignQtApp
    from ui.widgets.progress_panel import ProgressPanel
    model = ProgressModel(outcome="needs_input", result_message="哪个标题？", done_label=str(tmp_path))
    window = Mock()
    window._progress, window._running_worker = model, "gpt"
    window._cancel_requested, window._stderr_tail = False, []
    DashDesignQtApp.process_finished(window, 4, QProcess.ExitStatus.NormalExit)
    assert window.banner.show_message.call_args.args[0] == "info"
    window._collect_to_workspace.assert_not_called()
    window.gpt_page.finish_edit.assert_called_once_with(tmp_path, "needs_input")
    panel = ProgressPanel()
    panel.finalize(model, False, 10, "needs_input")
    assert "待补充" in panel.status_label.text() and not model.had_failure
    panel.close()


def test_ui_rejects_answer_for_newer_revision(source, tmp_path):
    from ui.pages.gpt_page import GptPage
    page = GptPage()
    page.gpt_source.setText(str(source))
    page.gpt_description.setPlainText("改标题")
    page.prepare_run()
    package = tmp_path / "pkg"
    package.mkdir()
    state = {"phase": "needs_input", "question": "哪个标题？", "revision": 1}
    workflow.save_json(package / "edit_state.json", state)
    page.finish_edit(package, "needs_input")
    page.clarification_answer.setPlainText("第四个")
    state["revision"] = 2
    workflow.save_json(package / "edit_state.json", state)
    with pytest.raises(ValueError, match="问题已过期"):
        page.prepare_run()
    page.close()


def test_worker_clarification_resume_utf8(source, tmp_path):
    received = []
    root = Path(__file__).resolve().parents[1]
    image_data = base64.b64encode(source.read_bytes()).decode("ascii")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, body))
            if self.path.endswith("/chat/completions"):
                payload = json.loads(body)
                context = json.loads(payload["messages"][1]["content"][0]["text"])
                intent = ready() if context["clarifications"] else question()
                data = {"choices": [{"message": {"content": json.dumps(intent, ensure_ascii=False)}}]}
            else:
                data = {"data": [{"b64_json": image_data}]}
            encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {**os.environ, "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
           "OPENAI_API_KEY": "fake-worker-key", "DASHDESIGN_PROGRESS": "1", "PYTHONUTF8": "1"}

    def worker(*args):
        return subprocess.run([sys.executable, str(root / "dashdesign_worker.py"), "--worker", "gpt",
                               "--api-mode", "edit", "--execute", *args], env=env, cwd=root,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    try:
        result = worker(str(source), "--output-dir", str(tmp_path / "中文任务"),
                        "--description", "改成蓝色", "--optimize-prompt", "--agent-model", "vision-test")
        assert result.returncode == 4, result.stderr
        events = [parse_progress_line(line) for line in result.stdout.splitlines()]
        final = next(event for event in events if event and event.kind == "result")
        assert final.outcome == "needs_input" and len(received) == 1
        package = Path(final.label)
        result = worker("--resume-package", str(package), "--answer-file", str(answer(package, "修改背景")))
        assert result.returncode == 0, result.stderr
        assert read(package / "edit_state.json")["phase"] == "success"
        assert [path for path, _ in received] == ["/v1/chat/completions", "/v1/chat/completions", "/v1/images/edits"]
        context = json.loads(json.loads(received[1][1])["messages"][1]["content"][0]["text"])
        assert context["clarifications"][0]["answer"] == "修改背景"
        assert "fake-worker-key" not in result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
