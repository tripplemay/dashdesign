"""Server-side document merge job: parse/ingest -> LLM extract -> merge report.

Reuses the Qt-free ``baseline`` extraction + merge core so the report is
identical to the desktop client's local merge. Two input modes, per the Phase B
privacy note: inline extracted ``text`` (originals never leave the operator's
machine) or a previously-uploaded ``document_id`` (parsed server-side from the
local document store). The chat callable is injected so this is testable without
a live gateway; production builds it from the server's own gateway credentials.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import url2pathname

from baseline.extract import extract_candidates
from baseline.ingest.parse import ParsedDocument, ParsedSection, parse_document
from baseline.merge import MergeReport, build_merge_report

ChatFactory = Callable[[Optional[str]], Callable]


def parsed_from_text(text: str, filename: str = "uploaded.txt") -> ParsedDocument:
    return ParsedDocument(
        file_name=filename,
        suffix=".txt",
        sections=[ParsedSection(section=filename, text=text)] if text.strip() else [],
    )


def parsed_from_storage_url(storage_url: str, filename: str) -> ParsedDocument:
    """Parse a document the server holds locally (file:// URI from LocalDocumentStore)."""
    parsed_url = urlparse(storage_url)
    if parsed_url.scheme != "file":
        raise ValueError(
            "该文档存储在对象存储中，服务端未下载；请改用内联 text 提交合并任务。"
        )
    from pathlib import Path

    # url2pathname is platform-correct (handles the Windows /C:/... form); plain
    # Path(urlparse(...).path) would yield an invalid \C:\... path on Windows.
    path = Path(url2pathname(parsed_url.path))
    return parse_document(path)


def report_to_dict(report: MergeReport) -> Dict[str, Any]:
    """JSON-serializable view of a MergeReport for storage / API transport."""
    changes: List[Dict[str, Any]] = []
    for c in report.changes:
        changes.append(
            {
                "target": c.target,
                "text": c.text,
                "confidence": c.confidence,
                "evidence": list(c.evidence),
                "governance": c.governance,
                "governance_label": c.governance_label,
                "accepted": c.accepted,
                "note": c.note,
            }
        )
    return {
        "changes": changes,
        "new_document": report.new_document,
        "new_evidence": report.new_evidence,
        "source_context_hint": report.source_context_hint,
        "summary": report.summary(),
    }


def run_extraction(
    parsed: ParsedDocument,
    current_baseline: Dict[str, Any],
    chat: Callable,
) -> Dict[str, Any]:
    if not parsed.sections:
        raise ValueError("未从文档/文本中解析出任何内容。")
    extraction = extract_candidates(parsed, current_baseline, chat)
    report = build_merge_report(current_baseline, extraction)
    return report_to_dict(report)
