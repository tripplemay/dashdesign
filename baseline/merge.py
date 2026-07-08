"""Three-way-ish baseline merge: classify extracted candidates, apply approved.

Design follows the research guardrails: additive by default (never auto-delete
existing content), governance-screened (blocked keywords hard-fail into the
B-side bucket, forbidden-claim triggers and low confidence require human
review), and append-only (produces a new draft linked by parent_version).
The GUI presents the report for review; nothing is auto-published.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from baseline import versioning

CONSUMER_TARGETS = (
    "consumer_baseline.core_messages",
    "consumer_baseline.parent_value",
    "consumer_baseline.student_value",
    "source_facts.consumer_safe_facts",
)
B_SIDE_TARGET = "source_facts.business_terms"
_ALL_TARGETS = CONSUMER_TARGETS + (B_SIDE_TARGET,)

# forbidden-claim 触发词（承诺升学/技能/收益类），命中即需人工，绝不自动进 C 端
_FORBIDDEN_TRIGGERS = (
    "保证", "承诺", "一定", "100%", "百分百", "包过", "包升学", "包就业",
    "稳赚", "收益", "回报", "必然", "确保",
)
_MIN_CONFIDENCE = 0.6

# governance 分类
OK = "ok"
BLOCKED = "blocked_keyword"
FORBIDDEN = "forbidden_claim"
LOW_CONF = "low_confidence"
B_SIDE = "b_side_only"

_GOV_LABEL = {
    OK: "可进入 C 端",
    BLOCKED: "命中禁用词（拦截）",
    FORBIDDEN: "疑似违规承诺（需人工）",
    LOW_CONF: "置信度低（需人工）",
    B_SIDE: "仅 B 端溯源",
}


@dataclass
class ProposedChange:
    target: str
    text: str
    confidence: float
    evidence: List[str]
    governance: str
    accepted: bool
    note: str = ""

    @property
    def governance_label(self) -> str:
        return _GOV_LABEL.get(self.governance, self.governance)


@dataclass
class MergeReport:
    changes: List[ProposedChange] = field(default_factory=list)
    new_document: Dict[str, Any] = field(default_factory=dict)
    new_evidence: List[Dict[str, Any]] = field(default_factory=list)
    source_context_hint: str = ""

    def accepted_changes(self) -> List[ProposedChange]:
        return [c for c in self.changes if c.accepted]

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for c in self.changes:
            counts[c.governance] = counts.get(c.governance, 0) + 1
        return counts


def _blocked_keywords(baseline: Dict[str, Any]) -> List[str]:
    consumer = baseline.get("consumer_baseline", {}) or {}
    return [str(k).strip() for k in consumer.get("blocked_keywords", []) if str(k).strip()]


def _existing_texts(baseline: Dict[str, Any], target: str) -> set:
    section, field_name = target.split(".", 1)
    items = (baseline.get(section, {}) or {}).get(field_name, []) or []
    return {str(i.get("text", "")).strip() for i in items if isinstance(i, dict)}


def _classify(target: str, text: str, confidence: float, blocked: List[str]) -> "tuple[str, bool, str]":
    if target == B_SIDE_TARGET:
        return B_SIDE, True, "记录为 B 端事实，仅供溯源，不进入 C 端出图"
    for kw in blocked:
        if kw and kw in text:
            return BLOCKED, False, f"命中禁用词“{kw}”，不可进入 C 端；如需保留请改入 B 端溯源"
    for trigger in _FORBIDDEN_TRIGGERS:
        if trigger in text:
            return FORBIDDEN, False, f"疑似违规承诺（含“{trigger}”），需人工确认后才可采纳"
    if confidence < _MIN_CONFIDENCE:
        return LOW_CONF, False, "抽取置信度较低，请人工核对原文后再采纳"
    return OK, True, ""


def build_merge_report(current: Dict[str, Any], extraction: Dict[str, Any]) -> MergeReport:
    """Classify extracted candidates against the current baseline (additive)."""
    blocked = _blocked_keywords(current)
    report = MergeReport(
        new_document=dict(extraction.get("document", {}) or {}),
        new_evidence=list(extraction.get("evidence", []) or []),
        source_context_hint=str(extraction.get("source_context_hint", "")),
    )
    for cand in extraction.get("candidates", []) or []:
        target = str(cand.get("target", ""))
        text = str(cand.get("text", "")).strip()
        if target not in _ALL_TARGETS or not text:
            continue
        if text in _existing_texts(current, target):
            continue  # 已存在，跳过
        try:
            confidence = float(cand.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        evidence = [str(e) for e in cand.get("evidence", []) or []]
        governance, accepted, note = _classify(target, text, confidence, blocked)
        report.changes.append(
            ProposedChange(
                target=target,
                text=text,
                confidence=confidence,
                evidence=evidence,
                governance=governance,
                accepted=accepted,
                note=note,
            )
        )
    return report


def apply_report(
    current: Dict[str, Any],
    report: MergeReport,
    today: str,
    existing_versions: List[str],
) -> Dict[str, Any]:
    """Produce a new draft with the report's accepted changes applied (additive)."""
    draft = versioning.new_draft_from(current, today, existing_versions)

    # 追加新文档
    doc = report.new_document
    if doc.get("document_id"):
        docs = draft.setdefault("source_documents", [])
        if not any(d.get("document_id") == doc["document_id"] for d in docs):
            docs.append(doc)
            # 追加了新的（可能是 B 端）文档 → 源上下文标记为混合
            draft["source_context"] = "mixed_docs"

    # 追加新证据（按 id 去重）
    ev_index = draft.setdefault("evidence_index", [])
    existing_ids = {e.get("id") for e in ev_index}
    accepted_ev = {e for c in report.accepted_changes() for e in c.evidence}
    for ev in report.new_evidence:
        if ev.get("id") in accepted_ev and ev.get("id") not in existing_ids:
            ev_index.append(ev)
            existing_ids.add(ev.get("id"))

    # 追加被采纳的候选
    for change in report.accepted_changes():
        section, field_name = change.target.split(".", 1)
        target_list = draft.setdefault(section, {}).setdefault(field_name, [])
        entry: Dict[str, Any] = {"text": change.text, "evidence": change.evidence}
        if change.confidence:
            entry["confidence"] = round(change.confidence, 2)
        if change.governance == B_SIDE:
            entry["notes"] = "B 端合作话术，不允许进入 C 端海报，仅供溯源。"
        target_list.append(entry)

    return draft
