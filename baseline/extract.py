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
    "你是项目基线的信息抽取助手。只依据给定文档原文抽取事实，严禁编造或推测。"
    "输出必须是一个 JSON 对象，且严格使用下面的字段结构（不要自创字段名）：\n"
    '{\n'
    '  "source_context_hint": "to_b_partnership_docs|to_c_marketing_docs|mixed_docs|manual",\n'
    '  "evidence": [ {"id": "ev_1", "section": "章节/页", "quote": "文档中的逐字原文片段"} ],\n'
    '  "candidates": [ {"target": "<字段>", "text": "改写后可用的一句话", "confidence": 0.0-1.0, "evidence": ["ev_1"]} ]\n'
    '}\n'
    "target 只能取以下之一：consumer_baseline.core_messages（面向受众的核心信息）、"
    "consumer_baseline.parent_value（家长/决策者价值）、consumer_baseline.student_value（学员/使用者价值）、"
    "source_facts.consumer_safe_facts（可安全对外的事实）、source_facts.business_terms（招商/加盟/代理/成交/定价/运营等 B 端经营内容，仅供溯源）。\n"
    "规则：每个 candidate 的 evidence 必须引用 evidence 列表里的 id；evidence.quote 必须逐字复制文档原文（不要改写、不要合并跨行）。"
    "招商/代理/成交/复购/定价/团队内训等 B 端内容一律放 source_facts.business_terms。"
    "禁止产出任何承诺升学、保证掌握技能、保证收益类表达。若某类抽不到就让对应数组为空，但不要编造。"
    "只输出该 JSON，不要额外解释。"
)

_EXAMPLE = (
    '示例（仅示意结构）：{"source_context_hint":"mixed_docs",'
    '"evidence":[{"id":"ev_1","section":"第 1 页","quote":"面向青少年的创作课程"}],'
    '"candidates":[{"target":"consumer_baseline.core_messages","text":"面向青少年的创作课程",'
    '"confidence":0.8,"evidence":["ev_1"]}]}'
)

# 已知的"非标准形状"别名 → 我们的 target，用于容错映射（模型偶尔自创字段名时抢救）。
_ALIAS_TARGETS = {
    "titles": "consumer_baseline.core_messages",
    "subtitles": "consumer_baseline.core_messages",
    "core_messages": "consumer_baseline.core_messages",
    "course_highlights": "consumer_baseline.student_value",
    "learning_content": "consumer_baseline.student_value",
    "student_value": "consumer_baseline.student_value",
    "parent_value": "consumer_baseline.parent_value",
    "safe_promotional_phrases": "source_facts.consumer_safe_facts",
    "consumer_safe_facts": "source_facts.consumer_safe_facts",
    "business_terms": "source_facts.business_terms",
}


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
        f"{_EXAMPLE}\n\n"
        f"文档《{parsed.file_name}》原文如下，请按上述结构抽取候选基线内容：\n\n{doc_text}"
    )
    messages = [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}]
    raw = _call(chat, messages)
    data = _adapt(_parse_json(raw), parsed)
    return _ground(data, parsed, document_meta or {})


def _call(chat: ChatFn, messages) -> str:
    """json_object 模式最通用；网关不支持时退回无 response_format。"""
    try:
        return chat(messages=messages, response_format={"type": "json_object"})
    except Exception:  # noqa: BLE001
        return chat(messages=messages)


def _adapt(data: Dict[str, Any], parsed: ParsedDocument) -> Dict[str, Any]:
    """If the model used its own shape, salvage recognizable arrays into our envelope."""
    if isinstance(data.get("candidates"), list) and data.get("candidates"):
        return data  # 已是我们的结构

    evidence: list = []
    candidates: list = []
    counter = {"n": 0}

    def add(target: str, item: Any) -> None:
        if isinstance(item, str):
            text, quote = item, item
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("value") or item.get("phrase") or "")
            ev = item.get("evidence")
            if isinstance(ev, dict):
                quote = str(ev.get("quote", "")) or text
            elif isinstance(ev, str):
                quote = ev
            else:
                quote = str(item.get("quote", "")) or text
        else:
            return
        text = text.strip()
        if not text:
            return
        counter["n"] += 1
        eid = f"ev_a{counter['n']}"
        evidence.append({"id": eid, "section": parsed.file_name, "quote": quote or text})
        candidates.append({"target": target, "text": text, "confidence": 0.6, "evidence": [eid]})

    def scan(container: Any) -> None:
        if not isinstance(container, dict):
            return
        for key, arr in container.items():
            target = _ALIAS_TARGETS.get(key)
            if target and isinstance(arr, list):
                for it in arr:
                    add(target, it)

    scan(data)
    scan(data.get("consumer_baseline"))
    scan(data.get("source_facts"))
    return {
        "source_context_hint": str(data.get("source_context_hint") or data.get("audience_mode") or "manual"),
        "evidence": evidence,
        "candidates": candidates,
    }


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
        norm_quote = _normalize(quote)
        # 至少 4 个非空白字符才算接地——否则空/纯空白 quote 归一为空串会
        # 因 "" in haystack 恒真而被误判为已接地。
        if len(norm_quote) >= 4 and norm_quote in haystack:
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
