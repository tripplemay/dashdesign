"""Durable, single-writer edit Agent workflow with explicit clarification resumes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import progress
from edit_prompt_agent import AGENT_VERSION, compile_edit_prompt, interpret_edit
from workflow_result import checkpoint, create_package_dir, finish, run_step


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def task_lock(package: Path):
    lock = package / ".edit.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("该编辑任务正在运行或曾被强制终止；请勿重复续跑，可重新建立任务") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def _snapshot(package: Path, state: dict) -> Path:
    source = (package / state["source_file"]).resolve()
    if package.resolve() not in source.parents or source_hash(source) != state["source_sha256"]:
        raise ValueError("任务原图已变更，不能沿用原转写结果")
    return source


def build_agent_package(source, output_dir, print_dpi, description, execute, size_override,
                        model, agent_model, resume_package, answer_file, *,
                        build_profile, save_preview, build_payload, execute_image_request) -> Path:
    if resume_package is not None:
        if not execute:
            raise ValueError("续跑必须使用 --execute")
        package = resume_package.resolve()
    else:
        if source is None or not (description or "").strip():
            raise ValueError("请提供原图和非空修改要求")
        if not agent_model.strip():
            raise ValueError("请指定支持图文输入的 Agent 模型")
        package = create_package_dir(output_dir / f"{source.stem}_agent_edit")

    with task_lock(package):
        if resume_package is None:
            (package / "input").mkdir()
            snapshot = package / "input" / ("source" + source.suffix.lower())
            shutil.copyfile(source, snapshot)
            profile = asdict(build_profile(source, print_dpi, size_override))
            if source_hash(source) != source_hash(snapshot):
                raise ValueError("复制期间原图发生变化，请重新开始")
            save_preview(snapshot, package / "source_preview.jpg")
            state = {"schema_version": 1, "phase": "prepared", "revision": 0,
                     "request": description.strip(), "history": [], "question": "",
                     "source_file": str(snapshot.relative_to(package)),
                     "source_sha256": source_hash(snapshot), "profile": profile,
                     "image_model": model, "agent_model": agent_model,
                     "agent_version": AGENT_VERSION, "image_request_started": False}
            save_json(package / "edit_state.json", state)
            save_json(package / "profile.json", profile)
        else:
            state = json.loads((package / "edit_state.json").read_text(encoding="utf-8"))
            if state.get("schema_version") != 1 or state.get("agent_version") != AGENT_VERSION:
                raise ValueError("任务版本不兼容，请重新开始")
            if state.get("image_request_started") or state.get("phase") not in {"prepared", "needs_input", "agent_failed"}:
                raise ValueError("该任务不可续跑；不能重复提交已开始的图片请求")
            if state["phase"] == "needs_input":
                if answer_file is None:
                    raise ValueError("请通过 --answer-file 提供澄清回答")
                answer = json.loads(answer_file.read_text(encoding="utf-8"))
                if answer.get("revision") != state["revision"] or not isinstance(answer.get("answer"), str) or not answer["answer"].strip():
                    raise ValueError("回答为空或已过期，请回答当前问题")
                state["history"].append({"question": state["question"], "answer": answer["answer"].strip()})
            elif answer_file is not None:
                raise ValueError("当前任务不接受澄清回答")
        snapshot = _snapshot(package, state)
        # Rebuild the derived preview on resume; only the verified snapshot is authoritative.
        if resume_package is not None:
            save_preview(snapshot, package / "source_preview.jpg")
        status = {"workflow": "agent_image_edit", "prompt_optimization": {"status": "prepared"},
                  "image_generation": {"status": "not_requested"}}
        record = {"model": state["image_model"], "agent_model": state["agent_model"],
                  "agent_version": AGENT_VERSION, "source_sha256": state["source_sha256"],
                  "api_mode": "edit", "execute_requested": execute}
        save_json(package / "generation_record.json", record)
        progress.plan(["解析原图", "理解修改要求", "编译提示词", "调用图片模型", "完成"])
        progress.stage(1)
        checkpoint(package, status)
        if not execute:
            (package / "prompt.md").write_text("待 Agent 转写，尚未调用任何模型。\n", encoding="utf-8")
            finish(package, status, False, [[status["image_generation"]]])
            return package

        state["phase"] = "analyzing"
        state["revision"] += 1
        save_json(package / "edit_state.json", state)
        progress.stage(2)
        intent = run_step(interpret_edit, package / "source_preview.jpg", state["request"],
                          state["history"], state["profile"], state["agent_model"], state["image_model"])
        status["prompt_optimization"] = intent
        save_json(package / f"agent_result_{state['revision']:03d}.json", intent)
        save_json(package / "agent_result.json", intent)
        if intent.get("status") == "needs_input":
            state.update(phase="needs_input", question=intent["question"])
            save_json(package / "edit_state.json", state)
            progress.interpretation(str(package), intent["summary"], "")
            finish(package, status, True, [[{"status": "needs_input", "reason": intent["question"]}]])
            return package
        if intent.get("status") != "ready":
            state["phase"] = "cancelled" if intent.get("status") == "cancelled" else "agent_failed"
            save_json(package / "edit_state.json", state)
            intent["reason"] = "图片编辑 Agent 转写未完成；未调用图片模型。" + str(intent.get("reason", ""))
            finish(package, status, True, [[intent]])
            return package

        progress.stage(3)
        prompt = compile_edit_prompt(intent, state["profile"]["gpt_image_size"])
        (package / "prompt.md").write_text(prompt + "\n", encoding="utf-8")
        # The callback accepts a profile-shaped object, keeping this module Qt-free.
        from types import SimpleNamespace
        payload = build_payload(SimpleNamespace(**state["profile"]), prompt, state["image_model"])
        save_json(package / "image_edit_request.json", payload)
        progress.interpretation(str(package), intent["summary"], prompt)
        _snapshot(package, state)
        # Persist before the network boundary. A timeout/kill must never be resumable as a new image call.
        state.update(phase="image_in_flight", image_request_started=True, question="")
        save_json(package / "edit_state.json", state)
        checkpoint(package, status)
        progress.stage(4)
        status["image_generation"] = run_step(execute_image_request, snapshot, payload,
                                              package / "gpt_image_edit_master.png", "edit")
        result_status = status["image_generation"].get("status")
        state["phase"] = {"generated": "success", "cancelled": "cancelled"}.get(result_status, "failed")
        save_json(package / "edit_state.json", state)
        progress.stage(5)
        finish(package, status, True, [[status["image_generation"]]])
        return package
