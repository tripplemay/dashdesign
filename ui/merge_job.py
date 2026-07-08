"""Background document-merge job (parse + LLM extract + build report).

Runs off the UI thread (the LLM call can take tens of seconds) and reports back
via Qt signals, mirroring ui/updater.py.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import QObject, Signal

from baseline.extract import extract_candidates
from baseline.ingest.parse import parse_document
from baseline.llm import make_chat
from baseline.merge import build_merge_report


class MergeSignals(QObject):
    done = Signal(object)  # MergeReport
    failed = Signal(str)


def run_merge_job(
    path: Path,
    current_baseline: Dict[str, Any],
    base_url: str,
    api_key: str,
    signals: MergeSignals,
    model: str = "",
) -> None:
    def worker() -> None:
        try:
            parsed = parse_document(path)
            if not parsed.sections:
                raise RuntimeError("未从文档中解析出任何文本。")
            chat = make_chat(base_url, api_key, model)
            extraction = extract_candidates(parsed, current_baseline, chat)
            report = build_merge_report(current_baseline, extraction)
            signals.done.emit(report)
        except Exception as exc:  # noqa: BLE001 - 反馈给 UI
            signals.failed.emit(str(exc))

    threading.Thread(target=worker, daemon=True).start()
