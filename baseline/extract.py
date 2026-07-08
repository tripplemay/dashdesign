"""LLM extraction of baseline candidates from a parsed document (grounded).

The chat client is injected (``chat(messages, response_format) -> str``) so the
core is testable without a network; the real gateway client lives in
``baseline/llm.py``. Every candidate must cite evidence whose ``quote`` is a
literal substring of the parsed document — ungrounded candidates are dropped,
which is what pins hallucination down (citation-grounded extraction).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict

from baseline.ingest.parse import ParsedDocument

ChatFn = Callable[..., str]

# 交给 LLM 的结构化输出约束（OpenAI 兼容 response_format=json_schema 用）
EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_context_hint", "evidence", "candidates"],
    "properties": {
        "source_context_hint": {
            "type": "string",
            "enum": ["to_b_partnership_docs", "to_c_marketing_docs", "mixed_docs", "manual"],
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "section", "quote"],
                "properties": {
                    "id": {"type": "string"},
                    "section": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target", "text", "confidence", "evidence"],
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": [
                            "consumer_baseline.core_messages",
                            "consumer_baseline.parent_value",
                            "consumer_baseline.student_value",
                            "source_facts.consumer_safe_facts",
                            "source_facts.business_terms",
                        ],
                    },
                    "text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

_SYSTEM = (
    "你是教育机构海报基线的抽取助手。只依据给定文档原文抽取事实，"
    "严禁编造或推测。每条抽取都必须给出 evidence，evidence.quote 必须是文档中的逐字原文片段。"
    "面向家长/学生（to-C）的可用表达放入 consumer_baseline.* 或 source_facts.consumer_safe_facts；"
    "招商/加盟/运营等 B 端经营话术只能放入 source_facts.business_terms（仅供溯源）。"
    "禁止产出任何承诺升学、保证掌握技能、保证收益类的表达。抽不到就返回空数组，不要编造。"
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def extract_candidates(
    parsed: ParsedDocument,
    current_baseline: Dict[str, Any],
    chat: ChatFn,
    document_meta: "Dict[str, Any] | None" = None,
) -> Dict[str, Any]:
    """Run grounded extraction. Returns an extraction dict for baseline.merge."""
    doc_text = parsed.full_text()
    project = current_baseline.get("project", {}) or {}
    user = (
        f"项目：{project.get('name', '')}\n"
        f"已有受众模式：{current_baseline.get('target_audience_mode', '')}\n\n"
        f"文档《{parsed.file_name}》原文如下，请抽取候选基线内容：\n\n{doc_text}"
    )
    messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]
    raw = chat(
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "baseline_extraction", "schema": EXTRACTION_SCHEMA, "strict": True},
        },
    )
    data = _parse_json(raw)
    return _ground(data, parsed, document_meta or {})


def _parse_json(raw: str) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # 兜底：截取第一个 JSON 对象
        match = re.search(r"\{.*\}", str(raw), re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    return {"source_context_hint": "manual", "evidence": [], "candidates": []}


def _ground(data: Dict[str, Any], parsed: ParsedDocument, document_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Drop evidence whose quote is not a literal substring; drop ungrounded candidates."""
    haystack = _normalize(parsed.full_text())
    doc_id = str(document_meta.get("document_id") or _slug(parsed.file_name))

    grounded_ev: Dict[str, Dict[str, Any]] = {}
    for ev in data.get("evidence", []) or []:
        quote = str(ev.get("quote", ""))
        if quote and _normalize(quote) in haystack:
            ev = dict(ev)
            ev["document_id"] = doc_id
            grounded_ev[str(ev.get("id"))] = ev

    candidates = []
    for cand in data.get("candidates", []) or []:
        refs = [e for e in (cand.get("evidence") or []) if e in grounded_ev]
        if not refs:
            continue  # 无接地证据 → 丢弃（防幻觉）
        cand = dict(cand)
        cand["evidence"] = refs
        candidates.append(cand)

    return {
        "source_context_hint": str(data.get("source_context_hint", "manual")),
        "document": {
            "document_id": doc_id,
            "file": document_meta.get("file") or parsed.file_name,
            "type": document_meta.get("type") or parsed.suffix.lstrip("."),
            "role": document_meta.get("role") or "supplementary_document",
            "status": "active",
        },
        "evidence": [grounded_ev[k] for k in grounded_ev if any(k in c["evidence"] for c in candidates)],
        "candidates": candidates,
    }


def _slug(name: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return stem or "uploaded_document"
