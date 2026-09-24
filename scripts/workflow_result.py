"""Durable, Qt-free result contract for image workflows."""

from __future__ import annotations

import json
import signal
import sys
import threading
from pathlib import Path

from PIL import Image

import progress
from image_api_client import sanitized_error

EXIT_CODES = {"prepared": 0, "success": 0, "failed": 1, "partial": 3, "cancelled": 130}


def create_package_dir(path: Path) -> Path:
    candidate = path
    index = 1
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            index += 1
            candidate = path.with_name(f"{path.name}_{index:02d}")


def checkpoint(package: Path, status: dict) -> None:
    temporary = package / "status.json.tmp"
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(package / "status.json")


def run_step(operation, *args, **kwargs) -> dict:
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _cancel_step)
    try:
        result = operation(*args, **kwargs)
        if not isinstance(result, dict):
            raise ValueError("Workflow step returned an invalid result")
        if result.get("status") == "generated":
            output = Path(result["output"])
            with Image.open(output) as image:
                image.verify()
        return result
    except KeyboardInterrupt:
        return {"status": "cancelled", "reason": "Cancelled; an in-flight upstream request may still complete."}
    except Exception as exc:
        return {"status": "error", **sanitized_error(exc)}
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _cancel_step(signum, frame) -> None:
    raise KeyboardInterrupt()


def finish(package: Path, status: dict, execute: bool, units: list[list[dict]], total: int | None = None) -> None:
    total = len(units) if total is None else total
    stages = [stage for unit in units for stage in unit]
    completed = sum(bool(unit) and all(s.get("status") == "generated" for s in unit) for unit in units)
    usable = sum(s.get("status") == "generated" for s in stages)
    if not execute:
        outcome = "prepared"
    elif any(s.get("status") == "cancelled" for s in stages):
        outcome = "cancelled"
    elif units and completed == total:
        outcome = "success"
    else:
        outcome = "partial" if usable else "failed"
    reasons = [str(s.get("reason") or s.get("error_type") or s.get("status")) for s in stages if s.get("status") != "generated"]
    labels = {"prepared": "请求包已准备，未调用 API", "success": "生成成功", "partial": "部分成功，已保留可用产物", "failed": "生成失败", "cancelled": "已取消，上游执行结果可能未知"}
    message = labels[outcome]
    if outcome not in {"prepared", "success"} and reasons:
        message += ": " + reasons[0]
    result = {"outcome": outcome, "completed": completed, "total": total, "message": message, "exit_code": EXIT_CODES[outcome]}
    status["result"] = result
    checkpoint(package, status)
    progress.result(str(package), result)
    if outcome in {"success", "prepared"}:
        progress.done(str(package))
    else:
        print(message, file=sys.stderr)


def package_exit_code(package: Path) -> int:
    return int(json.loads((package / "status.json").read_text(encoding="utf-8"))["result"]["exit_code"])
