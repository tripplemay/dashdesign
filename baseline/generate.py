"""Generate a full baseline from an uploaded document (new-domain bootstrap).

Unlike incremental merge (which appends to a same-domain baseline), this builds
a fresh baseline for a new business/domain: the LLM extracts audience mode,
positioning, course/product modules, consumer copy and B-side facts (all
citation-grounded), and we assemble them onto the template's *governance
scaffolding* (audience_transform_rules / visual_guidelines / prompt_policy /
governance / blocked_keywords / claims_policy) so the result is schema-valid and
still governed. Consumer/B-side copy goes through the same review + apply path.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Tuple

from baseline import merge, versioning
from baseline.extract import _adapt, _call, _normalize, _parse_json, _slug
from baseline.ingest.parse import ParsedDocument

ChatFn = Callable[..., str]

_SYSTEM = (
    "你是项目基线的全量抽取助手，要基于给定文档为一个新项目生成一套基线内容。"
    "只依据文档原文，严禁编造。输出一个 JSON 对象，严格使用以下字段（不要自创字段名）：\n"
    '{\n'
    '  "target_audience_mode": "to_c_parent_student|to_b_partnership|internal",\n'
    '  "source_context_hint": "to_b_partnership_docs|to_c_marketing_docs|mixed_docs|manual",\n'
    '  "positioning": {"text": "一句话项目定位", "quote": "文档逐字原文"},\n'
    '  "business_context": {"text": "经营/业务背景一句话", "quote": "文档逐字原文"},\n'
    '  "modules": [ {"name": "课程/产品模块名", "description": "一句话说明", "quote": "文档逐字原文"} ],\n'
    '  "evidence": [ {"id": "ev_1", "section": "章节/页", "quote": "文档逐字原文"} ],\n'
    '  "candidates": [ {"target": "<字段>", "text": "改写后可用的一句话", "confidence": 0.0-1.0, "evidence": ["ev_1"]} ]\n'
    '}\n'
    "target 只能取：consumer_baseline.core_messages、consumer_baseline.parent_value、"
    "consumer_baseline.student_value、source_facts.consumer_safe_facts、source_facts.business_terms。"
    "招商/代理/成交/复购/定价/团队内训等 B 端经营内容一律放 source_facts.business_terms。"
    "所有 quote 必须逐字复制文档原文；candidate.evidence 必须引用 evidence 列表里的 id。"
    "禁止产出承诺升学/掌握技能/收益类表达。只输出该 JSON。"
)


def _ground_quote(quote: str, haystack: str) -> bool:
    nq = _normalize(quote)
    return len(nq) >= 4 and nq in haystack


def generate_from_document(
    parsed: ParsedDocument,
    template: Dict[str, Any],
    baseline_id: str,
    name: str,
    chat: ChatFn,
    today: str,
    document_meta: "Dict[str, Any] | None" = None,
) -> Tuple[Dict[str, Any], "merge.MergeReport"]:
    """Return (skeleton_baseline, review_report). Finalize with finalize()."""
    import copy

    doc_text = parsed.full_text()
    haystack = _normalize(doc_text)
    doc_id = str((document_meta or {}).get("document_id") or _slug(parsed.file_name.rsplit(".", 1)[0]))

    user = f"文档《{parsed.file_name}》原文如下，请为新项目「{name}」抽取基线内容：\n\n{doc_text}"
    raw = _call(chat, [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}])
    data = _adapt(_parse_json(raw), parsed)

    # 证据池：模型给的 evidence（接地）+ 为 positioning/modules 合成的证据
    pool: Dict[str, Dict[str, Any]] = {}
    for ev in data.get("evidence", []) or []:
        q = str(ev.get("quote", ""))
        if _ground_quote(q, haystack):
            pool[str(ev.get("id"))] = {
                "id": str(ev.get("id")), "document_id": doc_id,
                "section": str(ev.get("section", parsed.file_name)), "quote": q,
            }

    counter = {"n": 0}

    def synth_evidence(quote: str, section: str) -> "str | None":
        if not _ground_quote(quote, haystack):
            return None
        counter["n"] += 1
        eid = f"ev_g{counter['n']}"
        pool[eid] = {"id": eid, "document_id": doc_id, "section": section, "quote": quote}
        return eid

    # 兜底证据（保证 evidence_index minItems>=1、可供占位字段引用）
    bootstrap_quote = parsed.sections[0].text[:60] if parsed.sections else parsed.file_name
    bootstrap_section = parsed.sections[0].section if parsed.sections else parsed.file_name

    def bootstrap_id() -> str:
        if "ev_bootstrap" not in pool:
            pool["ev_bootstrap"] = {
                "id": "ev_bootstrap", "document_id": doc_id,
                "section": bootstrap_section, "quote": bootstrap_quote,
            }
        return "ev_bootstrap"

    def evidenced(node: Any, fallback_text: str) -> Dict[str, Any]:
        node = node or {}
        text = str(node.get("text") or "").strip() or fallback_text
        eid = synth_evidence(str(node.get("quote", "")), parsed.file_name) or bootstrap_id()
        return {"text": text, "evidence": [eid], "confidence": 0.6}

    # --- 组装骨架：模板治理脚手架 + 文档抽取内容 ---
    skeleton = copy.deepcopy(template)
    skeleton["baseline_id"] = baseline_id
    skeleton["version"] = f"{today}.1"
    skeleton["parent_version"] = None
    skeleton["status"] = "draft"
    mode = str(data.get("target_audience_mode") or "").strip()
    if mode in ("to_c_parent_student", "to_b_partnership", "internal"):
        skeleton["target_audience_mode"] = mode
    skeleton["source_context"] = str(data.get("source_context_hint") or "mixed_docs")

    project = skeleton.setdefault("project", {})
    project["name"] = name
    project["core_positioning"] = evidenced(data.get("positioning"), f"{name}（项目定位待完善）")
    project["business_context"] = evidenced(data.get("business_context"), "业务背景待完善")

    skeleton["source_documents"] = [{
        "document_id": doc_id,
        "file": (document_meta or {}).get("file") or parsed.file_name,
        "type": parsed.suffix.lstrip("."),
        "role": "general_project_baseline",
        "status": "active",
    }]

    # 课程/产品体系（minItems>=1）
    modules: List[Dict[str, Any]] = []
    for i, m in enumerate(data.get("modules", []) or [], start=1):
        mname = str(m.get("name") or "").strip()
        if not mname:
            continue
        eid = synth_evidence(str(m.get("quote", "")), parsed.file_name) or bootstrap_id()
        modules.append({
            "id": _slug(mname) or f"module_{i}",
            "name": mname,
            "description": str(m.get("description") or mname),
            "student_outcomes": [],
            "evidence": [eid],
        })
    if not modules:
        modules = [{
            "id": "module_1", "name": "待完善模块", "description": "请人工补充课程/产品模块",
            "student_outcomes": [], "evidence": [bootstrap_id()],
        }]
    source_facts = skeleton.setdefault("source_facts", {})
    source_facts["course_system"] = modules
    for cleared in ("business_terms", "consumer_safe_facts", "education_values"):
        source_facts[cleared] = []
    for raw_key in ("b_side_customers", "decision_makers", "end_users"):
        source_facts.setdefault("raw_audience", {})[raw_key] = []

    consumer = skeleton.setdefault("consumer_baseline", {})
    consumer["positioning"] = evidenced(data.get("positioning"), f"{name}（定位待完善）")
    for cleared in ("core_messages", "parent_value", "student_value", "course_modules"):
        consumer[cleared] = []

    # positioning/modules 至少会引用一个 bootstrap 证据，pool 必非空（满足 minItems>=1）
    skeleton["evidence_index"] = list(pool.values())

    # --- 供审校的字段候选（consumer/business 文案）---
    extraction = {
        "source_context_hint": skeleton["source_context"],
        "document": {},  # 文档已在骨架里
        "evidence": list(pool.values()),
        "candidates": data.get("candidates", []) or [],
    }
    report = merge.build_merge_report(skeleton, extraction)
    return skeleton, report


def finalize(skeleton: Dict[str, Any], report: "merge.MergeReport") -> Dict[str, Any]:
    """Apply the reviewed candidates into the skeleton (fresh version, no parent)."""
    baseline = __import__("copy").deepcopy(skeleton)
    merge.apply_accepted(baseline, report)
    return baseline
